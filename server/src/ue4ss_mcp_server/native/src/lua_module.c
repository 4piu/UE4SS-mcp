/* Lua 5.4 C-extension binding for pipe_server.c.
 *
 * `require("ue4ssmcp_pipe")` from main.lua gets a module table with one
 * function, `create_server(name) -> instance | nil, err`. The returned
 * userdata is deliberately non-blocking/poll-based -- UE4SS gives each
 * mod exactly one dedicated async thread (confirmed via UE4SS's own
 * source), so a blocking call here would stall every other pending
 * client/accept for as long as it blocks (confirmed live during real
 * multi-agent testing). So: `:accept_async()`/`:read_line_async()`
 * start a background thread this module manages itself (independent of
 * UE4SS's async thread), and `:poll_accept()`/`:poll_read_line()` check
 * completion without blocking -- main.lua drives these from a
 * lightweight recurring tick. `:write(str)` stays synchronous: writing
 * to a pipe with buffer space is effectively instantaneous, unlike
 * accept/read which wait on an external actor.
 */
#include "pipe_server.h"

#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"

#include <string.h>

#define INSTANCE_METATABLE "ue4ssmcp_pipe.instance"
#define READ_LINE_BUF_SIZE 65536

typedef struct
{
    PipeInstance* inst; /* NULL after :close() */
} InstanceUserdata;

static InstanceUserdata* check_instance(lua_State* L, int idx)
{
    return (InstanceUserdata*)luaL_checkudata(L, idx, INSTANCE_METATABLE);
}

static int require_open(lua_State* L, InstanceUserdata* ud)
{
    if (ud->inst == NULL) {
        lua_pushnil(L);
        lua_pushliteral(L, "pipe instance already closed");
        return 0;
    }
    return 1;
}

static int l_instance_accept_async(lua_State* L)
{
    InstanceUserdata* ud = check_instance(L, 1);
    if (!require_open(L, ud)) {
        return 2;
    }
    char errbuf[256];
    int ok = pipe_accept_async(ud->inst, errbuf, sizeof(errbuf));
    if (!ok) {
        lua_pushboolean(L, 0);
        lua_pushstring(L, errbuf);
        return 2;
    }
    lua_pushboolean(L, 1);
    return 1;
}

static int l_instance_poll_accept(lua_State* L)
{
    InstanceUserdata* ud = check_instance(L, 1);
    if (!require_open(L, ud)) {
        return 2;
    }
    char errbuf[256];
    PipePollResult r = pipe_poll_accept(ud->inst, errbuf, sizeof(errbuf));
    if (r == PIPE_POLL_PENDING) {
        lua_pushliteral(L, "pending");
        return 1;
    }
    if (r == PIPE_POLL_ERROR) {
        lua_pushliteral(L, "error");
        lua_pushstring(L, errbuf);
        return 2;
    }
    lua_pushliteral(L, "ready");
    return 1;
}

static int l_instance_read_line_async(lua_State* L)
{
    InstanceUserdata* ud = check_instance(L, 1);
    if (!require_open(L, ud)) {
        return 2;
    }
    char errbuf[256];
    int ok = pipe_read_line_async(ud->inst, errbuf, sizeof(errbuf));
    if (!ok) {
        lua_pushboolean(L, 0);
        lua_pushstring(L, errbuf);
        return 2;
    }
    lua_pushboolean(L, 1);
    return 1;
}

static int l_instance_poll_read_line(lua_State* L)
{
    InstanceUserdata* ud = check_instance(L, 1);
    if (!require_open(L, ud)) {
        return 2;
    }
    char errbuf[256];
    static char line_buf[READ_LINE_BUF_SIZE];
    PipePollResult r = pipe_poll_read_line(ud->inst, line_buf, sizeof(line_buf), errbuf, sizeof(errbuf));
    if (r == PIPE_POLL_PENDING) {
        lua_pushliteral(L, "pending");
        return 1;
    }
    if (r == PIPE_POLL_ERROR) {
        lua_pushliteral(L, "error");
        lua_pushstring(L, errbuf);
        return 2;
    }
    lua_pushliteral(L, "ready");
    lua_pushstring(L, line_buf);
    return 2;
}

static int l_instance_write(lua_State* L)
{
    InstanceUserdata* ud = check_instance(L, 1);
    if (!require_open(L, ud)) {
        return 2;
    }
    size_t len = 0;
    const char* data = luaL_checklstring(L, 2, &len);
    char errbuf[256];
    int ok = pipe_write(ud->inst, data, len, errbuf, sizeof(errbuf));
    if (!ok) {
        lua_pushboolean(L, 0);
        lua_pushstring(L, errbuf);
        return 2;
    }
    lua_pushboolean(L, 1);
    return 1;
}

static int l_instance_cancel(lua_State* L)
{
    InstanceUserdata* ud = check_instance(L, 1);
    if (ud->inst != NULL) {
        pipe_cancel(ud->inst);
    }
    return 0;
}

static int l_instance_close(lua_State* L)
{
    InstanceUserdata* ud = check_instance(L, 1);
    if (ud->inst != NULL) {
        pipe_close(ud->inst);
        ud->inst = NULL;
    }
    return 0;
}

static int l_instance_gc(lua_State* L)
{
    /* Safety net if Lua GCs an instance without an explicit :close() --
     * should not be relied on for timely cleanup (GC timing is
     * unspecified), main.lua must still call :close() itself. */
    InstanceUserdata* ud = check_instance(L, 1);
    if (ud->inst != NULL) {
        pipe_close(ud->inst);
        ud->inst = NULL;
    }
    return 0;
}

static const luaL_Reg instance_methods[] = {
    {"accept_async", l_instance_accept_async},
    {"poll_accept", l_instance_poll_accept},
    {"read_line_async", l_instance_read_line_async},
    {"poll_read_line", l_instance_poll_read_line},
    {"write", l_instance_write},
    {"cancel", l_instance_cancel},
    {"close", l_instance_close},
    {"__gc", l_instance_gc},
    {NULL, NULL}};

static int l_create_server(lua_State* L)
{
    const char* name = luaL_checkstring(L, 1);
    char errbuf[256];
    PipeInstance* inst = pipe_create_instance(name, errbuf, sizeof(errbuf));
    if (inst == NULL) {
        lua_pushnil(L);
        lua_pushstring(L, errbuf);
        return 2;
    }

    InstanceUserdata* ud = (InstanceUserdata*)lua_newuserdata(L, sizeof(InstanceUserdata));
    ud->inst = inst;
    luaL_getmetatable(L, INSTANCE_METATABLE);
    lua_setmetatable(L, -2);
    return 1;
}

static const luaL_Reg module_functions[] = {
    {"create_server", l_create_server},
    {NULL, NULL}};

__declspec(dllexport) int luaopen_ue4ssmcp_pipe(lua_State* L)
{
    luaL_newmetatable(L, INSTANCE_METATABLE);
    lua_pushvalue(L, -1);
    lua_setfield(L, -2, "__index");
    luaL_setfuncs(L, instance_methods, 0);
    lua_pop(L, 1);

    luaL_newlib(L, module_functions);
    return 1;
}
