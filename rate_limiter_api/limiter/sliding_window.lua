-- Accepted timestamps for one API client/user pair.
-- Redis runs this script atomically.
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_us = tonumber(ARGV[2])
local should_consume = ARGV[3] == '1'

-- ARGV[4] is an internal test clock. HTTP clients cannot supply it.
local now_us = tonumber(ARGV[4])
if not now_us then
    local clock = redis.call('TIME')
    now_us = tonumber(clock[1]) * 1000000 + tonumber(clock[2])
end

-- Keep list order even if the wall clock moves backward slightly.
local newest = redis.call('LINDEX', key, -1)
if newest then
    now_us = math.max(now_us, tonumber(newest))
end

-- Window: (now - window, now]. Exactly window-old entries expire.
local cutoff = now_us - window_us
while true do
    local oldest = redis.call('LINDEX', key, 0)
    if not oldest or tonumber(oldest) > cutoff then
        break
    end
    redis.call('LPOP', key)
end

local used = redis.call('LLEN', key)
local allowed = used < limit
if allowed and should_consume then
    -- Fixed decimal formatting avoids scientific-notation precision loss.
    redis.call('RPUSH', key, string.format('%.0f', now_us))
    redis.call('PEXPIRE', key, math.ceil(window_us / 1000))
    used = used + 1
end

local wait_us = 0
local next_us = 0
if used >= limit then
    -- Also correct if a stored rule's limit was reduced below current usage.
    local expiring = redis.call('LINDEX', key, used - limit)
    next_us = tonumber(expiring) + window_us
    wait_us = math.max(0, next_us - now_us)
end

local allowed_number = 0
if allowed then allowed_number = 1 end
return {allowed_number, used, math.max(0, limit - used), wait_us, next_us}
