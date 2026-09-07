-- UE4SS MCP bridge mod.
--
-- Connects out to ue4ss-mcp-server over a named pipe so an AI coding
-- agent can inspect/interact with this running game. Installed by
-- ue4ss-mcp-server's install_bridge_mod tool -- the TOKEN below is
-- generated per-install and must match what the server expects; don't
-- hand-edit it.
--
-- M3 skeleton: proves the handshake round-trip. No reflection request
-- handling yet (that's M4+). The initial connect attempt below runs
-- synchronously at mod-load time -- safe because a named pipe client
-- open fails fast (no server listening) rather than blocking, and the
-- handshake response wait is a bounded, one-time cost during mod load,
-- not a sustained hold on the game thread. Retries after the first
-- attempt are scheduled via ExecuteWithDelay so they don't block anything.

local PIPE_NAME = "\\\\.\\pipe\\ue4ss-mcp"
local TOKEN = "__BRIDGE_TOKEN__"

-- Not having the MCP server running is a normal state (you won't always
-- be actively using the tool while playing), so back off instead of
-- hammering the log every 2s forever: doubles each failed attempt up to
-- a 30s ceiling, then holds there.
local INITIAL_RETRY_DELAY_MS = 2000
local MAX_RETRY_DELAY_MS = 30000
local retry_delay_ms = INITIAL_RETRY_DELAY_MS

local function try_handshake()
    local pipe, open_err = io.open(PIPE_NAME, "r+b")
    if not pipe then
        return false, open_err
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
    pipe:close()

    if response == nil then
        return false, "no response from server"
    end
    return true, response
end

local function connect_with_retry()
    local ok, result = try_handshake()
    if ok then
        print(string.format("[UE4SSMCPBridge] Connected: %s\n", result))
        retry_delay_ms = INITIAL_RETRY_DELAY_MS
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
