local st = require "util.stanza";
local um_is_admin = require "core.usermanager".is_admin;

-- Weak room keys prevent stale state after a room is destroyed.
local pending_affiliations =
    setmetatable({}, { __mode = "k" });

local active_moderators =
    setmetatable({}, { __mode = "k" });

local function is_admin(user_jid)
    return user_jid
        and um_is_admin(user_jid, module.host);
end

local function is_true(value)
    return value == true
        or value == "true"
        or value == 1
        or value == "1";
end

local function is_token_moderator(session)
    if not session or not session.auth_token then
        return false;
    end

    local user = session.jitsi_meet_context_user;

    if not user then
        return false;
    end

    return is_true(user.moderator);
end

local function set_pending_affiliation(
    room,
    occupant_jid,
    affiliation
)
    local room_pending = pending_affiliations[room];

    if not room_pending then
        room_pending = {};
        pending_affiliations[room] = room_pending;
    end

    room_pending[occupant_jid] = affiliation;
end

local function take_pending_affiliation(
    room,
    occupant_jid
)
    local room_pending = pending_affiliations[room];

    if not room_pending then
        return nil;
    end

    local affiliation = room_pending[occupant_jid];
    room_pending[occupant_jid] = nil;

    if next(room_pending) == nil then
        pending_affiliations[room] = nil;
    end

    return affiliation;
end

local function add_active_moderator(
    room,
    occupant_jid
)
    local moderators = active_moderators[room];

    if not moderators then
        moderators = {};
        active_moderators[room] = moderators;
    end

    moderators[occupant_jid] = true;
end

local function remove_active_moderator(
    room,
    occupant_jid
)
    local moderators = active_moderators[room];

    if not moderators then
        return;
    end

    moderators[occupant_jid] = nil;

    if next(moderators) == nil then
        active_moderators[room] = nil;
    end
end

local function room_has_token_moderator(room)
    local moderators = active_moderators[room];

    return moderators ~= nil
        and next(moderators) ~= nil;
end

-- Defense in depth:
-- direct non-administrative room creation requires moderator=true.
-- In normal Jitsi operation Jicofo creates the MUC and is allowed here.
module:hook("muc-room-pre-create", function(event)
    local origin = event.origin;
    local stanza = event.stanza;

    local actor_jid =
        origin and origin.full_jid or nil;

    local room_jid =
        stanza and stanza.attr and stanza.attr.to or nil;

    if actor_jid and is_admin(actor_jid) then
        module:log(
            "debug",
            "Administrative room creation allowed: actor:%s room:%s",
            tostring(actor_jid),
            tostring(room_jid)
        );
        return;
    end

    if is_token_moderator(origin) then
        module:log(
            "info",
            "Direct moderator room creation allowed: actor:%s room:%s",
            tostring(actor_jid),
            tostring(room_jid)
        );
        return;
    end

    module:log(
        "warn",
        "Direct room creation denied: actor:%s room:%s",
        tostring(actor_jid),
        tostring(room_jid)
    );

    if origin and origin.send and stanza then
        origin.send(
            st.error_reply(
                stanza,
                "cancel",
                "not-allowed",
                "Moderator token required to create this room"
            )
        );
    end

    return true;
end, 98);

-- token_verification runs first at priority 99.
-- Moderators may join immediately.
-- Guests may join only after a JWT moderator is present.
module:hook("muc-occupant-pre-join", function(event)
    local room = event.room;
    local occupant = event.occupant;
    local origin = event.origin;
    local stanza = event.stanza;

    if not room or not occupant then
        return;
    end

    if is_admin(occupant.bare_jid) then
        return;
    end

    if is_token_moderator(origin) then
        set_pending_affiliation(
            room,
            occupant.bare_jid,
            "owner"
        );

        module:log(
            "info",
            "JWT moderator permitted to join: %s in %s",
            tostring(occupant.bare_jid),
            tostring(room.jid)
        );

        return;
    end

    if not room_has_token_moderator(room) then
        module:log(
            "warn",
            "Guest join denied before moderator: %s in %s",
            tostring(occupant.bare_jid),
            tostring(room.jid)
        );

        if origin and origin.send and stanza then
            origin.send(
                st.error_reply(
                    stanza,
                    "cancel",
                    "not-allowed",
                    "Conference moderator has not joined yet"
                )
            );
        end

        return true;
    end

    set_pending_affiliation(
        room,
        occupant.bare_jid,
        "member"
    );

    module:log(
        "info",
        "Guest permitted after moderator arrival: %s in %s",
        tostring(occupant.bare_jid),
        tostring(room.jid)
    );
end, 98);

module:hook("muc-occupant-joined", function(event)
    local room = event.room;
    local occupant = event.occupant;

    if not room or not occupant then
        return;
    end

    local affiliation =
        take_pending_affiliation(
            room,
            occupant.bare_jid
        );

    if not affiliation then
        return;
    end

    room:set_affiliation(
        true,
        occupant.bare_jid,
        affiliation
    );

    if affiliation == "owner" then
        add_active_moderator(
            room,
            occupant.bare_jid
        );
    end

    module:log(
        "info",
        "Assigned JWT affiliation %s to %s in %s",
        affiliation,
        tostring(occupant.bare_jid),
        tostring(room.jid)
    );
end, 2);

module:hook("muc-occupant-left", function(event)
    local room = event.room;
    local occupant = event.occupant;

    if not room or not occupant then
        return;
    end

    take_pending_affiliation(
        room,
        occupant.bare_jid
    );

    local moderators = active_moderators[room];

    if moderators
        and moderators[occupant.bare_jid] then

        remove_active_moderator(
            room,
            occupant.bare_jid
        );

        module:log(
            "info",
            "JWT moderator left: %s in %s; moderator_present:%s",
            tostring(occupant.bare_jid),
            tostring(room.jid),
            tostring(room_has_token_moderator(room))
        );
    end
end);
