// Strict JWT invitation deployments must not expose Jitsi's built-in
// invitation controls because they generate a bare room URL without JWT.
//
// Add this setting to the active Jitsi Meet config.js file:

config.disableInviteFunctions = true;
