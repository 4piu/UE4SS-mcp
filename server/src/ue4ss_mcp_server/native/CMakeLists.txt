cmake_minimum_required(VERSION 3.20)
project(ue4ssmcp_native C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

add_library(pipe_server STATIC src/pipe_server.c src/pipe_server.h)
target_include_directories(pipe_server PUBLIC src)

add_executable(test_pipe_server test/test_pipe_server.c)
target_link_libraries(test_pipe_server PRIVATE pipe_server)

# Lua 5.4.7 core, vendored verbatim from UE4SS's own deps/first/LuaRaw
# (see lua54/README -- exact same source, same compiled-as-C setting,
# no LUA_BUILD_AS_DLL) so its lua_State layout matches byte-for-byte
# what UE4SS's own (separately compiled, unexported) copy produces.
# UE4SS.dll does not export any lua_*/luaL_* symbols (confirmed via
# `dumpbin /exports` against a real installed UE4SS.dll) -- this is the
# only way for a companion native module to speak the Lua C API at all.
file(GLOB LUA54_SOURCES lua54/*.c)
list(REMOVE_ITEM LUA54_SOURCES
    "${CMAKE_CURRENT_SOURCE_DIR}/lua54/lua.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/lua54/luac.c")
add_library(lua54 STATIC ${LUA54_SOURCES})
target_include_directories(lua54 PUBLIC lua54)

# The actual UE4SS mod DLL: `require("ue4ssmcp_pipe")` from main.lua
# loads this (UE4SS extends package.cpath to include the mod's own
# Scripts/ dir, confirmed in UE4SS/src/Mod/LuaMod.cpp).
add_library(ue4ssmcp_pipe SHARED src/lua_module.c)
target_link_libraries(ue4ssmcp_pipe PRIVATE pipe_server lua54)
set_target_properties(ue4ssmcp_pipe PROPERTIES PREFIX "")
