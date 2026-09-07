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

local PIPE_NAME = "\\\\.\\pipe\\ue4ss-mcp"
local TOKEN = "__BRIDGE_TOKEN__"

-- Not having the MCP server running is a normal state (you won't always
-- be actively using the tool while playing), so back off instead of
-- hammering the log every 2s forever: doubles each failed attempt up to
-- a 30s ceiling, then holds there.
local INITIAL_RETRY_DELAY_MS = 2000
local MAX_RETRY_DELAY_MS = 30000
local retry_delay_ms = INITIAL_RETRY_DELAY_MS

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

local function op_describe_object(params)
    local obj = resolve_handle(params.handle)
    if not obj then
        return false, "HANDLE_EXPIRED"
    end

    local limit = math.min(tonumber(params.limit) or 50, 200)
    local offset = tonumber(params.cursor) or 0

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
    -- Both queue for the next update cycle rather than acting
    -- immediately, so returning normally here (letting the response for
    -- *this* request go out first) is safe even when reloading ourselves.
    if mod_name == _SELF_MOD_NAME then
        RestartCurrentMod()
    else
        RestartMod(mod_name)
    end
    return true, { queued = true, mod_name = mod_name }
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
-- Transport: handshake (synchronous, at mod load) then a persistent
-- serve loop (async-thread reads, game-thread request handling).
--------------------------------------------------------------------------

local function write_message(pipe, tbl)
    pipe:write(json.encode(tbl) .. "\n")
    pipe:flush()
end

local connect_with_retry -- forward declaration, used by the serve loop's disconnect path

local serve_loop
local function schedule_serve(pipe)
    ExecuteAsync(function() serve_loop(pipe) end)
end

serve_loop = function(pipe)
    local line = pipe:read("*l")
    if line == nil then
        pcall(function() pipe:close() end)
        print("[UE4SSMCPBridge] Disconnected, reconnecting...\n")
        connect_with_retry()
        return
    end

    local pok, req = pcall(json.decode, line)
    if pok and type(req) == "table" and req.type == "request" then
        ExecuteInGameThread(function()
            local response = handle_request(req)
            local wok = pcall(write_message, pipe, response)
            if not wok then
                pcall(function() pipe:close() end)
                print("[UE4SSMCPBridge] Write failed, reconnecting...\n")
                connect_with_retry()
                return
            end
            schedule_serve(pipe)
        end)
    else
        serve_loop(pipe)
    end
end

local function try_handshake()
    local pipe, open_err = io.open(PIPE_NAME, "r+b")
    if not pipe then
        return nil, open_err
    end

    local ue4ss_major, ue4ss_minor, ue4ss_hotfix = 0, 0, 0
    if UE4SS ~= nil then
        ue4ss_major, ue4ss_minor, ue4ss_hotfix = UE4SS.GetVersion()
    end
    local engine_major = UnrealVersion.GetMajor()
    local engine_minor = UnrealVersion.GetMinor()

    local handshake = string.format(
        '{"type":"handshake","token":"%s","ue4ss_version":"%d.%d.%d","engine_version":"%d.%d"}',
        TOKEN, ue4ss_major, ue4ss_minor, ue4ss_hotfix, engine_major, engine_minor
    )
    pipe:write(handshake .. "\n")
    pipe:flush()

    local response = pipe:read("*l")
    if response == nil then
        pcall(function() pipe:close() end)
        return nil, "no response from server"
    end
    return pipe, response
end

connect_with_retry = function()
    local pipe, result = try_handshake()
    if pipe then
        print(string.format("[UE4SSMCPBridge] Connected: %s\n", result))
        retry_delay_ms = INITIAL_RETRY_DELAY_MS
        schedule_serve(pipe)
        return
    end
    print(string.format(
        "[UE4SSMCPBridge] Connect failed (%s), retrying in %dms...\n",
        tostring(result), retry_delay_ms
    ))
    ExecuteWithDelay(retry_delay_ms, connect_with_retry)
    retry_delay_ms = math.min(retry_delay_ms * 2, MAX_RETRY_DELAY_MS)
end

connect_with_retry()
