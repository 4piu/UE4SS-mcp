-- UE4SS MCP bridge mod.
--
-- Connects out to ue4ss-mcp-server over a named pipe so an AI coding
-- agent can inspect/interact with this running game. Installed by
-- ue4ss-mcp-server's install_bridge_mod tool -- the TOKEN below is
-- generated per-install and must match what the server expects; don't
-- hand-edit it.
--
-- M4: the pipe stays open after the handshake and serves reflection
-- requests (find_object/describe_object/call_function) for the life of
-- the connection. Reads happen on UE4SS's async thread (ExecuteAsync) so
-- they never block the game thread; each decoded request is handled on
-- the game thread (ExecuteInGameThread) since UObject/reflection access
-- must happen there, then the response is written and the next read is
-- scheduled. No native JSON library ships with UE4SS's Lua, so this file
-- includes a small self-contained encoder/decoder below.
--
-- M5: adds register_hook/unregister_hook/watch_new_object/
-- poll_hook_events/reload_mod. Hook and watch fires happen at
-- unpredictable times (whenever the game calls the hooked function or
-- constructs the watched class) rather than in response to a request,
-- so they're buffered here and drained via poll_hook_events -- nothing
-- is ever pushed to the server outside of a request's own response.
--
-- M6: adds dump_and_index (triggers UE4SS's own bulk dumpers --
-- DumpAllActors/DumpAllObjects/GenerateSDK/GenerateUHTCompatibleHeaders
-- -- and nothing more; the output files can be huge, so reading and
-- indexing them happens entirely on the Python side) and exec_lua (a
-- last resort: compiles and runs arbitrary Lua, size-capping any string
-- result).
--
-- Transport flip: this mod now HOSTS the named pipe (via a small
-- companion native module, ue4ssmcp_pipe.dll -- Lua's stdlib alone
-- can't create a Windows named pipe server) instead of dialing out to
-- the MCP server. The server connects OUT to this mod's own uniquely-
-- named pipe, and lets more than one agent attach to the same running
-- game at once (nMaxInstances > 1 on the native side).
--
-- Confirmed live: UE4SS gives each mod exactly ONE dedicated async
-- thread (ExecuteAsync/ExecuteWithDelay/LoopAsync all funnel through
-- the same single per-mod thread, one callback at a time -- this is
-- UE4SS's own design, not a bug). A blocking accept()/read() there
-- would stall every other pending client/accept for as long as it
-- blocks -- this reliably starved concurrent requests during real
-- multi-agent testing. So the native module is non-blocking/poll-based
-- (accept_async/poll_accept, read_line_async/poll_read_line -- see its
-- own header for why) and this file drives it from one lightweight
-- recurring tick (self-rescheduling via ExecuteWithDelay) rather than a
-- blocking chain -- the tick itself never blocks, so it can service the
-- pending listener AND every connected client's next-read in the same
-- pass without any of them waiting on each other.

local pipe_native = require("ue4ssmcp_pipe")

local PIPE_NAME = "__BRIDGE_PIPE_NAME__"
local TOKEN = "__BRIDGE_TOKEN__"
local TICK_INTERVAL_MS = 20

--------------------------------------------------------------------------
-- Minimal JSON encode/decode. Only what the bridge protocol needs:
-- objects, arrays (dense, 1-based), strings, numbers, booleans, null.
--------------------------------------------------------------------------

local json = {}

local ESCAPES = {
    ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t',
}

local function encode_string(s)
    local escaped = s:gsub('[%c"\\]', function(c)
        return ESCAPES[c] or string.format("\\u%04x", string.byte(c))
    end)
    return '"' .. escaped .. '"'
end

local function is_array(t)
    local count = 0
    for _ in pairs(t) do
        count = count + 1
    end
    if count == 0 then
        return true
    end
    local seq = 0
    for _ in ipairs(t) do
        seq = seq + 1
    end
    return seq == count
end

function json.encode(value)
    local t = type(value)
    if value == nil then
        return "null"
    elseif t == "boolean" then
        return value and "true" or "false"
    elseif t == "number" then
        return tostring(value)
    elseif t == "string" then
        return encode_string(value)
    elseif t == "table" then
        if is_array(value) then
            local parts = {}
            for i, v in ipairs(value) do
                parts[i] = json.encode(v)
            end
            return "[" .. table.concat(parts, ",") .. "]"
        else
            local parts = {}
            for k, v in pairs(value) do
                table.insert(parts, encode_string(tostring(k)) .. ":" .. json.encode(v))
            end
            return "{" .. table.concat(parts, ",") .. "}"
        end
    end
    return "null"
end

local decode_value -- forward declaration

local function skip_ws(s, i)
    local _, j = s:find("^%s*", i)
    return j + 1
end

local function decode_string(s, i)
    local j = i + 1
    local out = {}
    while true do
        local c = s:sub(j, j)
        if c == "" then
            error("unterminated JSON string")
        elseif c == '"' then
            return table.concat(out), j + 1
        elseif c == "\\" then
            local esc = s:sub(j + 1, j + 1)
            local simple = { n = "\n", t = "\t", r = "\r", b = "\b", f = "\f", ['"'] = '"', ["\\"] = "\\", ["/"] = "/" }
            if esc == "u" then
                local hex = s:sub(j + 2, j + 5)
                local code = tonumber(hex, 16) or 63
                out[#out + 1] = string.char(code < 256 and code or 63)
                j = j + 6
            elseif simple[esc] then
                out[#out + 1] = simple[esc]
                j = j + 2
            else
                out[#out + 1] = esc
                j = j + 2
            end
        else
            out[#out + 1] = c
            j = j + 1
        end
    end
end

local function decode_number(s, i)
    local m = s:match("^-?%d+%.?%d*[eE]?[+%-]?%d*", i)
    return tonumber(m), i + #m
end

decode_value = function(s, i)
    i = skip_ws(s, i)
    local c = s:sub(i, i)
    if c == '"' then
        return decode_string(s, i)
    elseif c == "{" then
        local obj = {}
        i = skip_ws(s, i + 1)
        if s:sub(i, i) == "}" then
            return obj, i + 1
        end
        while true do
            i = skip_ws(s, i)
            local key
            key, i = decode_string(s, i)
            i = skip_ws(s, i)
            if s:sub(i, i) ~= ":" then
                error("expected ':' in JSON object")
            end
            i = skip_ws(s, i + 1)
            local val
            val, i = decode_value(s, i)
            obj[key] = val
            i = skip_ws(s, i)
            local d = s:sub(i, i)
            if d == "," then
                i = i + 1
            elseif d == "}" then
                return obj, i + 1
            else
                error("expected ',' or '}' in JSON object")
            end
        end
    elseif c == "[" then
        local arr = {}
        local n = 0
        i = skip_ws(s, i + 1)
        if s:sub(i, i) == "]" then
            return arr, i + 1
        end
        while true do
            local val
            val, i = decode_value(s, i)
            n = n + 1
            arr[n] = val
            i = skip_ws(s, i)
            local d = s:sub(i, i)
            if d == "," then
                i = i + 1
            elseif d == "]" then
                return arr, i + 1
            else
                error("expected ',' or ']' in JSON array")
            end
        end
    elseif s:sub(i, i + 3) == "true" then
        return true, i + 4
    elseif s:sub(i, i + 4) == "false" then
        return false, i + 5
    elseif s:sub(i, i + 3) == "null" then
        return nil, i + 4
    else
        return decode_number(s, i)
    end
end

function json.decode(s)
    return decode_value(s, 1)
end

--------------------------------------------------------------------------
-- Object handle registry. Handles are scoped to this Lua state (survive
-- a pipe reconnect, since only the mod reload/restart destroys the Lua
-- state) but are checked for engine-level liveness on every resolve via
-- IsValid() -- that's the real signal for "this UObject no longer
-- exists" (level unload, actor destroyed, etc.), not the pipe's own
-- connection state.
--------------------------------------------------------------------------

local handles = {}
local handle_counter = 0

local function register_handle(obj)
    handle_counter = handle_counter + 1
    local id = string.format("obj_%08x", handle_counter)
    handles[id] = obj
    return id
end

local function resolve_handle(id)
    local obj = handles[id]
    if obj == nil then
        return nil
    end
    local ok, valid = pcall(function() return obj:IsValid() end)
    if not ok or not valid then
        handles[id] = nil
        return nil
    end
    return obj
end

--------------------------------------------------------------------------
-- Value serialization for describe_object/call_function results. Nested
-- UObjects become new handles (never inlined/recursed) to keep responses
-- small -- the agent drills down with another describe_object call.
--------------------------------------------------------------------------

local function serialize_value(v)
    if v == nil then
        return { type = "nil" }
    end
    local lt = type(v)
    if lt == "boolean" or lt == "number" or lt == "string" then
        return { type = lt, value = v }
    end

    local ok_str, s = pcall(function()
        if v.ToString then
            return v:ToString()
        end
        return nil
    end)
    if ok_str and s ~= nil then
        return { type = "string", value = s }
    end

    local ok_obj, full_name = pcall(function()
        if v.IsValid and v.GetFullName and v:IsValid() then
            return v:GetFullName()
        end
        return nil
    end)
    if ok_obj and full_name ~= nil then
        return { type = "object", handle = register_handle(v), full_name = full_name }
    end

    -- A plain Lua table (not a UE4SS object -- those already matched one
    -- of the probes above) has no ToString/IsValid to key off of, so
    -- without this branch it falls straight to the tostring() fallback
    -- below and comes back as an opaque "table: 0x..." address instead
    -- of its actual contents. json.encode already knows how to render a
    -- table of {type,value}-tagged wrappers as a JSON array or object
    -- (see is_array) -- recurse into it here rather than opaquing it.
    if lt == "table" then
        local out = {}
        if is_array(v) then
            for i, item in ipairs(v) do
                out[i] = serialize_value(item)
            end
        else
            for k, item in pairs(v) do
                out[tostring(k)] = serialize_value(item)
            end
        end
        return { type = "table", value = out }
    end

    local ok_repr, repr = pcall(tostring, v)
    return { type = "opaque", repr = ok_repr and repr or "?" }
end

local function brief_object(obj, name)
    if not name then
        local ok, n = pcall(function() return obj:GetFName():ToString() end)
        name = ok and n or "?"
    end
    local ok_class, class_name = pcall(function() return obj:GetClass():GetFName():ToString() end)
    return { handle = register_handle(obj), class = ok_class and class_name or "?", name = name }
end

--------------------------------------------------------------------------
-- Request handlers. Each returns (ok, result_or_error_message).
--------------------------------------------------------------------------

local function op_find_object(params)
    local limit = math.min(tonumber(params.limit) or 20, 200)
    local offset = tonumber(params.cursor) or 0

    if params.path then
        local obj = StaticFindObject(params.path)
        if not obj or not obj:IsValid() then
            return true, { items = {}, returned = 0, total_matched = 0, truncated = false, cursor = nil }
        end
        return true, {
            items = { brief_object(obj) },
            returned = 1,
            total_matched = 1,
            truncated = false,
            cursor = nil,
        }
    end

    if not params.class then
        return false, "find_object requires 'class' or 'path'"
    end

    local all = FindAllOf(params.class)
    if not all then
        return true, { items = {}, returned = 0, total_matched = 0, truncated = false, cursor = nil }
    end

    local pattern = params.name_pattern
    if pattern then
        pattern = pattern:lower()
    end

    local items = {}
    local matched = 0
    for _, obj in pairs(all) do
        local ok, name = pcall(function() return obj:GetFName():ToString() end)
        if ok and (not pattern or name:lower():find(pattern, 1, true)) then
            local idx = matched
            matched = matched + 1
            if idx >= offset and #items < limit then
                table.insert(items, brief_object(obj, name))
            end
        end
    end

    local next_offset = offset + #items
    local truncated = next_offset < matched
    return true, {
        items = items,
        returned = #items,
        total_matched = matched,
        truncated = truncated,
        cursor = truncated and tostring(next_offset) or nil,
    }
end

-- A UFunction IS a UStruct, so ForEachProperty on it (not on its
-- GetClass(), which just describes "what kind of thing is a UFunction"
-- generically) enumerates its actual parameters -- confirmed live: for
-- /Script/Engine.PlayerController:ClientMessage this correctly yields
-- (S: StrProperty, Type: NameProperty, MsgLifeTime: FloatProperty),
-- exactly the real signature. This is the only way to learn a
-- UFunction's parameter types before calling it, since call_function's
-- own error only reports a count mismatch, never a type one.
local function op_describe_function(obj, params, offset, limit)
    local parameters = {}
    local total = 0
    obj:ForEachProperty(function(prop)
        total = total + 1
        local idx = total - 1
        if idx >= offset and #parameters < limit then
            local ok_name, name = pcall(function() return prop:GetFName():ToString() end)
            local ok_type, prop_type = pcall(function() return prop:GetClass():GetFName():ToString() end)
            table.insert(parameters, {
                name = ok_name and name or "?",
                property_type = ok_type and prop_type or "?",
            })
        end
    end)

    local next_offset = offset + #parameters
    local truncated = next_offset < total
    local ok_full, full_name = pcall(function() return obj:GetFullName() end)

    return {
        handle = nil, -- filled in by the caller, which already has params.handle
        full_name = ok_full and full_name or "?",
        is_function = true,
        parameters = parameters,
        returned = #parameters,
        total_matched = total,
        truncated = truncated,
        cursor = truncated and tostring(next_offset) or nil,
    }
end

local function op_describe_object(params)
    local obj = resolve_handle(params.handle)
    if not obj then
        return false, "HANDLE_EXPIRED"
    end

    local limit = math.min(tonumber(params.limit) or 50, 200)
    local offset = tonumber(params.cursor) or 0

    local ok_type, ue_type = pcall(function() return obj:type() end)
    if ok_type and ue_type == "UFunction" then
        local result = op_describe_function(obj, params, offset, limit)
        result.handle = params.handle
        return true, result
    end

    local class = obj:GetClass()
    local props = {}
    local total = 0
    class:ForEachProperty(function(prop)
        total = total + 1
        local idx = total - 1
        if idx >= offset and #props < limit then
            local ok_name, name = pcall(function() return prop:GetFName():ToString() end)
            name = ok_name and name or "?"
            local ok_val, value = pcall(function() return obj[name] end)
            local serialized = ok_val and serialize_value(value) or { type = "error", repr = "unreadable" }
            serialized.name = name
            table.insert(props, serialized)
        end
    end)

    local next_offset = offset + #props
    local truncated = next_offset < total

    local ok_full, full_name = pcall(function() return obj:GetFullName() end)
    local ok_class_name, class_name = pcall(function() return class:GetFullName() end)

    return true, {
        handle = params.handle,
        full_name = ok_full and full_name or "?",
        class = ok_class_name and class_name or "?",
        is_function = false,
        properties = props,
        returned = #props,
        total_matched = total,
        truncated = truncated,
        cursor = truncated and tostring(next_offset) or nil,
    }
end

local function op_call_function(params)
    local obj = resolve_handle(params.handle)
    if not obj then
        return false, "HANDLE_EXPIRED"
    end

    local fn_name = params.function_name
    if not fn_name then
        return false, "call_function requires 'function_name'"
    end

    local fn = obj[fn_name]
    if fn == nil then
        return false, string.format("no such function/member '%s'", fn_name)
    end

    local args = params.args or {}
    local n = 0
    for _ in pairs(args) do
        n = n + 1
    end

    local ok, result_or_err = pcall(fn, obj, table.unpack(args, 1, n))
    if not ok then
        return false, tostring(result_or_err)
    end
    return true, { result = serialize_value(result_or_err) }
end

--------------------------------------------------------------------------
-- Hook/watch event buffers. RegisterHook/NotifyOnNewObject fire whenever
-- the game calls the hooked function or constructs the watched class --
-- unpredictable timing, potentially many times per frame. Rather than
-- push each fire over the wire (which the rest of this protocol
-- deliberately never does), fires are buffered here and drained via the
-- normal pull-based poll_hook_events request, same as every other op.
--
-- Buffers use a monotonic sequence number, not a plain array index, so
-- that a cursor issued before an eviction still means the same thing
-- afterward -- evicting from the front of a plain array (to cap memory)
-- would silently shift every later index and invalidate outstanding
-- cursors.
--------------------------------------------------------------------------

local _MAX_BUFFERED_EVENTS = 200

local hooks = {}
local watches = {}
local hook_counter = 0
local watch_counter = 0

local function new_event_buffer()
    return { events = {}, oldest_seq = 1, newest_seq = 0, dropped = 0 }
end

local function push_event(buf, event)
    buf.newest_seq = buf.newest_seq + 1
    buf.events[buf.newest_seq] = event
    if (buf.newest_seq - buf.oldest_seq + 1) > _MAX_BUFFERED_EVENTS then
        buf.events[buf.oldest_seq] = nil
        buf.oldest_seq = buf.oldest_seq + 1
        buf.dropped = buf.dropped + 1
    end
end

-- `cursor` is the last sequence number the caller has already seen (0 or
-- nil = nothing seen yet), NOT a plain offset -- and unlike the
-- snapshot-style tools, the returned cursor is never nil, since there's
-- always a "resume from here next time" position for a live stream even
-- when nothing new fired this poll.
local function poll_event_buffer(buf, cursor, limit)
    local after = math.max(tonumber(cursor) or 0, buf.oldest_seq - 1)
    local items = {}
    local seq = after + 1
    while seq <= buf.newest_seq and #items < limit do
        table.insert(items, buf.events[seq])
        seq = seq + 1
    end
    local truncated = seq <= buf.newest_seq
    local dropped = buf.dropped
    buf.dropped = 0
    return {
        items = items,
        returned = #items,
        total_matched = buf.newest_seq - buf.oldest_seq + 1,
        truncated = truncated,
        cursor = tostring(seq - 1),
        dropped_since_last_poll = dropped,
    }
end

local function serialize_param(p)
    local ok, v = pcall(function() return p:get() end)
    return serialize_value(ok and v or nil)
end

local function op_register_hook(params)
    local ufunction_name = params.ufunction_name
    if not ufunction_name then
        return false, "register_hook requires 'ufunction_name'"
    end
    local requested_when = params.when or "both"

    -- RegisterHook's callback-slot meaning depends on the path: for
    -- native (/Script/) UFunctions, slot 1 = pre and slot 2 = post; for
    -- everything else (Blueprint functions), slot 1 actually fires
    -- *after* the call and slot 2 does nothing at all. Tagging events by
    -- what actually happens (not by which slot fired) keeps `when` in
    -- the recorded event honest regardless of function type.
    local is_native = ufunction_name:sub(1, 8) == "/Script/"

    hook_counter = hook_counter + 1
    local hook_id = string.format("hook_%08x", hook_counter)
    local buf = new_event_buffer()

    local function record(tag, context, ...)
        if requested_when ~= "both" and requested_when ~= tag then
            return
        end
        local args = { ... }
        local serialized_args = {}
        for i, p in ipairs(args) do
            serialized_args[i] = serialize_param(p)
        end
        push_event(buf, {
            when = tag,
            context = serialize_param(context),
            args = serialized_args,
        })
    end

    local function slot1_cb(context, ...)
        record(is_native and "pre" or "post", context, ...)
    end
    local function slot2_cb(context, ...)
        -- Only meaningful for native (/Script/) paths -- a no-op call
        -- for everything else, matching RegisterHook's own semantics.
        record("post", context, ...)
    end

    local pre_id, post_id = RegisterHook(ufunction_name, slot1_cb, slot2_cb)
    if pre_id == nil then
        return false, string.format("RegisterHook failed for '%s' -- does it exist in memory yet?", ufunction_name)
    end

    hooks[hook_id] = { buf = buf, ufunction_name = ufunction_name, pre_id = pre_id, post_id = post_id }
    return true, { hook_id = hook_id }
end

local function op_unregister_hook(params)
    local id = params.hook_id
    local hook = hooks[id]
    if hook then
        -- Stops the native hook, but deliberately doesn't drop `hook`
        -- itself -- any events it already buffered but that haven't been
        -- polled yet must stay reachable, or unregistering would silently
        -- discard data the agent never got a chance to see.
        if not hook.unregistered then
            pcall(UnregisterHook, hook.ufunction_name, hook.pre_id, hook.post_id)
            hook.unregistered = true
        end
        return true, { unregistered = true }
    end
    local watch = watches[id]
    if watch then
        -- NotifyOnNewObject has no direct unregister call -- this flag is
        -- what the callback checks on its next fire to actually stop
        -- (see op_watch_new_object). Same "keep the buffer" reasoning.
        watch.unregistered = true
        return true, { unregistered = true }
    end
    return false, "unknown hook_id/watch_id"
end

local function op_watch_new_object(params)
    local class_name = params.class_name
    if not class_name then
        return false, "watch_new_object requires 'class_name'"
    end

    watch_counter = watch_counter + 1
    local watch_id = string.format("watch_%08x", watch_counter)
    watches[watch_id] = { buf = new_event_buffer(), unregistered = false }

    NotifyOnNewObject(class_name, function(obj)
        local watch = watches[watch_id]
        if watch == nil or watch.unregistered then
            return true
        end
        push_event(watch.buf, { object = serialize_value(obj) })
        return false
    end)

    return true, { watch_id = watch_id }
end

local function op_poll_hook_events(params)
    local id = params.hook_id
    local buf
    if hooks[id] then
        buf = hooks[id].buf
    elseif watches[id] then
        buf = watches[id].buf
    end
    if not buf then
        return false, "unknown hook_id/watch_id"
    end
    local limit = math.min(tonumber(params.limit) or 20, 200)
    return true, poll_event_buffer(buf, params.cursor, limit)
end

local _SELF_MOD_NAME = "UE4SSMCPBridge"

local function op_reload_mod(params)
    local mod_name = params.mod_name
    if not mod_name then
        return false, "reload_mod requires 'mod_name'"
    end
    -- Restarting THIS mod from within a request THIS mod is currently
    -- handling queues the teardown of the very call stack that's still
    -- trying to write this response and schedule the next read --
    -- unlike restarting some other mod from outside its call stack,
    -- which RestartMod handles fine. Confirmed live: this reliably
    -- produced real connection instability, not just occasional
    -- flakiness. No known real use case justifies the risk (an agent
    -- never edits the bridge's own code; picking up a bridge update
    -- already requires a human's "Restart All Mods" click regardless,
    -- same as any mod's first load), so this is refused outright
    -- rather than attempted.
    if mod_name == _SELF_MOD_NAME then
        return false, "cannot reload the bridge mod from within itself -- ask a human to click \"Restart All Mods\" in the UE4SS console, or relaunch the game"
    end
    RestartMod(mod_name)
    return true, { queued = true, mod_name = mod_name }
end

-- Triggers only -- the resulting file(s) can be huge (hundreds of
-- thousands of lines / thousands of headers), so reading and indexing
-- them happens entirely on the Python side, which has direct filesystem
-- access to the game install and never needs to pipe that content
-- through here at all. Looked up by name via _G rather than bound
-- directly so a UE4SS build missing one of these (e.g. an older
-- version without GenerateUHTCompatibleHeaders) fails cleanly instead
-- of erroring at mod-load time.
local DUMP_TRIGGER_NAMES = {
    actors = "DumpAllActors",
    objects = "DumpAllObjects",
    sdk = "GenerateSDK",
    uht = "GenerateUHTCompatibleHeaders",
}

local function op_dump_and_index(params)
    local fn_name = DUMP_TRIGGER_NAMES[params.kind]
    if not fn_name then
        return false, "dump_and_index: kind must be one of 'actors', 'objects', 'sdk', 'uht'"
    end
    local fn = _G[fn_name]
    if not fn then
        return false, string.format("%s is not available in this UE4SS build", fn_name)
    end
    local ok, err = pcall(fn)
    if not ok then
        return false, tostring(err)
    end
    return true, { kind = params.kind, triggered = true }
end

local _EXEC_LUA_MAX_STRING_LEN = 4000

local function op_exec_lua(params)
    local code = params.code
    if not code then
        return false, "exec_lua requires 'code'"
    end

    local fn, compile_err = load(code)
    if not fn then
        return false, "compile error: " .. tostring(compile_err)
    end

    local ok, result = pcall(fn)
    if not ok then
        return false, tostring(result)
    end

    local serialized = serialize_value(result)
    if serialized.type == "string" and #serialized.value > _EXEC_LUA_MAX_STRING_LEN then
        serialized.value = serialized.value:sub(1, _EXEC_LUA_MAX_STRING_LEN)
        serialized.truncated = true
    end
    return true, { result = serialized }
end

local OPS = {
    find_object = op_find_object,
    describe_object = op_describe_object,
    call_function = op_call_function,
    register_hook = op_register_hook,
    unregister_hook = op_unregister_hook,
    watch_new_object = op_watch_new_object,
    poll_hook_events = op_poll_hook_events,
    reload_mod = op_reload_mod,
    dump_and_index = op_dump_and_index,
    exec_lua = op_exec_lua,
}

local function handle_request(req)
    local handler = OPS[req.op]
    if not handler then
        return { type = "response", id = req.id, ok = false, error = "unknown op: " .. tostring(req.op) }
    end

    local pok, ok, result_or_err = pcall(handler, req.params or {})
    if not pok then
        return { type = "response", id = req.id, ok = false, error = tostring(ok) }
    elseif ok then
        return { type = "response", id = req.id, ok = true, result = result_or_err }
    else
        return { type = "response", id = req.id, ok = false, error = tostring(result_or_err) }
    end
end

--------------------------------------------------------------------------
-- Transport: this mod hosts the pipe (via pipe_native, non-blocking --
-- see the file header for why). One listener instance is always
-- accepting the next client; each connected client is tracked in
-- `clients` through its own handshake -> request/response cycle, all
-- driven from one recurring, non-blocking tick.
--------------------------------------------------------------------------

-- The pending "waiting for the next client" instance: { native = ... }.
local listening = nil

-- Connected clients: { native = ..., state = "handshake"|"request",
-- awaiting_line = bool (a read_line_async is currently in flight) }.
-- Marked `dead = true` for removal rather than spliced out mid-iteration.
local clients = {}

local function start_listening()
    local inst, create_err = pipe_native.create_server(PIPE_NAME)
    if not inst then
        print(string.format("[UE4SSMCPBridge] create_server failed (%s)\n", tostring(create_err)))
        return nil
    end
    local ok, accept_err = inst:accept_async()
    if not ok then
        print(string.format("[UE4SSMCPBridge] accept_async failed to start (%s)\n", tostring(accept_err)))
        pcall(function() inst:close() end)
        return nil
    end
    return { native = inst }
end

local function start_next_read(client_entry)
    local ok, err = client_entry.native:read_line_async()
    if not ok then
        print(string.format("[UE4SSMCPBridge] read_line_async failed to start (%s)\n", tostring(err)))
        pcall(function() client_entry.native:close() end)
        client_entry.dead = true
        return
    end
    client_entry.awaiting_line = true
end

local function handle_handshake_line(client_entry, line)
    local pok, hs = pcall(json.decode, line)
    if not pok or type(hs) ~= "table" or hs.type ~= "handshake" or hs.token ~= TOKEN then
        pcall(function() client_entry.native:write('{"type":"handshake_ack","ok":false,"error":"bad token"}\n') end)
        pcall(function() client_entry.native:close() end)
        client_entry.dead = true
        print("[UE4SSMCPBridge] Rejected a client: bad handshake token\n")
        return
    end

    local ue4ss_major, ue4ss_minor, ue4ss_hotfix = 0, 0, 0
    if UE4SS ~= nil then
        ue4ss_major, ue4ss_minor, ue4ss_hotfix = UE4SS.GetVersion()
    end
    local engine_major = UnrealVersion.GetMajor()
    local engine_minor = UnrealVersion.GetMinor()
    local ack = string.format(
        '{"type":"handshake_ack","ok":true,"ue4ss_version":"%d.%d.%d","engine_version":"%d.%d"}',
        ue4ss_major, ue4ss_minor, ue4ss_hotfix, engine_major, engine_minor
    )
    if not client_entry.native:write(ack .. "\n") then
        pcall(function() client_entry.native:close() end)
        client_entry.dead = true
        return
    end

    print("[UE4SSMCPBridge] Client connected\n")
    client_entry.state = "request"
    start_next_read(client_entry)
end

local function handle_request_line(client_entry, line)
    local pok, req = pcall(json.decode, line)
    if not (pok and type(req) == "table" and req.type == "request") then
        -- Malformed line -- skip it and keep waiting, same tolerance the
        -- pre-flip transport had.
        start_next_read(client_entry)
        return
    end

    -- No read is in flight while a request is being handled on the game
    -- thread, so the tick loop leaves this client alone until it starts
    -- the next read itself (from inside this same callback).
    client_entry.awaiting_line = false
    ExecuteInGameThread(function()
        local response = handle_request(req)
        local wok = client_entry.native:write(json.encode(response) .. "\n")
        if not wok then
            pcall(function() client_entry.native:close() end)
            client_entry.dead = true
            print("[UE4SSMCPBridge] Write failed, client disconnected\n")
            return
        end
        start_next_read(client_entry)
    end)
end

local function tick()
    if listening then
        local status, err = listening.native:poll_accept()
        if status == "ready" then
            local client_entry = { native = listening.native, state = "handshake" }
            start_next_read(client_entry)
            table.insert(clients, client_entry)
            listening = start_listening()
        elseif status == "error" then
            print(string.format("[UE4SSMCPBridge] accept failed (%s)\n", tostring(err)))
            pcall(function() listening.native:close() end)
            listening = start_listening()
        end
        -- "pending": nothing to do yet.
    else
        listening = start_listening()
    end

    for i = #clients, 1, -1 do
        local c = clients[i]
        if c.dead then
            table.remove(clients, i)
        elseif c.awaiting_line then
            local status, a, b = c.native:poll_read_line()
            if status == "ready" then
                local line = a
                if c.state == "handshake" then
                    handle_handshake_line(c, line)
                else
                    handle_request_line(c, line)
                end
            elseif status == "error" then
                pcall(function() c.native:close() end)
                table.remove(clients, i)
            end
            -- "pending": nothing to do yet.
        end
    end

    ExecuteWithDelay(TICK_INTERVAL_MS, tick)
end

listening = start_listening()
ExecuteWithDelay(TICK_INTERVAL_MS, tick)
