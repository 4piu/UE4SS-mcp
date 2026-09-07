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

local OPS = {
    find_object = op_find_object,
    describe_object = op_describe_object,
    call_function = op_call_function,
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
