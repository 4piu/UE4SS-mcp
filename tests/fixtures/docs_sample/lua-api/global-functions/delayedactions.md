# Delayed Actions

The Delayed Actions API provides a comprehensive timer system for executing code after delays, with full control over timing, pausing, cancellation, and looping. This system is similar to Unreal Engine's timer system.

## Overview

All delayed actions execute on the game thread and return handles that can be used to control them. Actions are owned by the mod that created them, preventing cross-mod interference.

## Creating Delayed Actions

### ExecuteInGameThreadWithDelay

Executes a callback after a specified delay.

**Overload 1: Auto-generated handle**
```lua
local handle = ExecuteInGameThreadWithDelay(delayMs, callback)
```

| # | Type | Information |
|---|------|-------------|
| 1 | integer | Delay in milliseconds before executing |
| 2 | function | Callback to execute |
| Return | integer | Handle for the created action |

**Overload 2: User-provided handle (UE Delay-style)**
```lua
ExecuteInGameThreadWithDelay(handle, delayMs, callback)
```

| # | Type | Information |
|---|------|-------------|
| 1 | integer | Handle (from `MakeActionHandle()`) |
| 2 | integer | Delay in milliseconds |
| 3 | function | Callback to execute |

This overload only creates the action if the handle is not already active. This mirrors Unreal Engine's `Delay` node behavior.

## Examples

### Simple Delay

```lua
ExecuteInGameThreadWithDelay(2000, function()
    print("This prints after 2 seconds\n")
end)
```
