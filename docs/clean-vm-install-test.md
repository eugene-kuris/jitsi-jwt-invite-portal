# Clean-VM installation test

Status: **passed**

Validation date: 2026-07-24

Release under test: `v0.1.0-rc2`
Release commit: `74ce5fc17039015e14a49768f4019e57b892d69b`

## Purpose

This test validated that Jitsi JWT Invite Portal can be installed from a
tagged public release on a clean Debian 12 virtual machine with Jitsi Meet
installed from Debian packages.

The test also validated a deployment where the Jitsi server has a private
IPv4 address behind NAT instead of a public IPv4 address assigned directly
to a server interface.

## Test environment

- Debian 12 Bookworm minimal installation;
- 4 vCPU;
- 32 GiB RAM;
- 160 GiB ext4 system disk;
- Jitsi Meet installed from Debian packages;
- stable private IPv4 address on the Jitsi server;
- stable public IPv4 address on the edge firewall;
- public DNS resolving to the edge firewall;
- HTTPS terminated by a trusted reverse proxy on the edge firewall;
- public UDP port 10000 forwarded separately to Jitsi Videobridge;
- system timezone set to `Europe/Kyiv`.

## Validated network topology

```text
Internet clients
    |
    +-- TCP 443 --> edge HTTPS reverse proxy --> Jitsi nginx
    |
    +-- UDP 10000 --> edge DNAT --> Jitsi Videobridge
```

The HTTPS reverse proxy handles web and signaling traffic. It does not proxy
Jitsi Videobridge media, so UDP port 10000 requires a separate forwarding
rule.

## Validated software versions

| Component | Version |
|---|---:|
| Jitsi Meet | `2.0.11031-1` |
| Jitsi Meet Prosody | `1.0.9268-1` |
| Jitsi Meet Web | `1.0.9268-1` |
| Jitsi Meet Web Config | `1.0.9268-1` |
| Jitsi Videobridge 2 | `2.3-295-g8d5c0037b-1` |
| Jicofo | `1.0-1183-1` |
| Prosody | `13.0.6` |
| coturn | `4.6.1-1` |

Prosody reported Lua 5.2 on this host. This differs from the Lua 5.4 runtime
used by the original reference deployment and confirms that the active
Prosody Lua runtime must be detected rather than assumed.

## Defects found during validation

The clean installation exposed the following documentation defects:

1. Network prerequisites and supported topologies were not documented.
2. Deployments with a public IPv4 address directly on the server were not
   distinguished from deployments using a private IPv4 address behind NAT.
3. UDP port 10000 forwarding and Jitsi Videobridge static address mapping
   were not documented for NAT deployments.
4. The installation procedure enabled the portal service separately from
   starting it instead of using `systemctl enable --now`.
5. The acceptance procedure did not require a Jitsi Videobridge health check.
6. A two-client test could remain in peer-to-peer mode and therefore did not
   prove that Jitsi Videobridge and its NAT configuration worked.
7. The acceptance test did not require three simultaneous clients.

These defects were converted into installation requirements in
[`installation.md`](installation.md).

## NAT/JVB failure reproduced and resolved

The initial NAT deployment passed signaling and role checks but failed when
the conference required Jitsi Videobridge media routing.

Observed behavior:

- the moderator entered successfully;
- Prosody admitted the guest with member affiliation;
- browser sessions then failed when media was routed through the bridge;
- Jicofo and Jitsi Videobridge reported `No valid IP addresses available for
  harvesting`.

The Jitsi server had a private IPv4 address, while the public IPv4 address
belonged to the edge firewall. UDP port 10000 forwarding existed, but Jitsi
Videobridge did not know which public ICE address to advertise.

The failure was resolved by adding a static private-to-public address mapping
under `ice4j.harvest.mapping.static-mappings` in
`/etc/jitsi/videobridge/jvb.conf`, then restarting Jitsi Videobridge.

After the correction:

- the UDP harvester initialized on the private server address and port 10000;
- the Jitsi Videobridge health endpoint returned HTTP 200;
- no new valid-address or bridge-health errors appeared;
- the three-client audio/video conference completed successfully.

The reusable configuration procedure is documented in
[`installation.md`](installation.md).

## Acceptance results

The clean-VM deployment passed the following checks:

- strict JWT authentication rejected a bare room URL;
- a guest could not enter the room before a JWT moderator;
- the JWT moderator received owner affiliation and moderator controls;
- guests admitted after the moderator received member affiliation only;
- permanent and revocable invitation links worked as designed;
- the portal backend remained accessible only through its Unix socket;
- direct `/invite/` access without a trusted organizer identity returned
  HTTP 403;
- Jitsi Videobridge returned HTTP 200 from `/about/health`;
- Jitsi Videobridge listened on UDP port 10000;
- no new bridge-health, ICE harvesting or valid-address errors appeared;
- all required services remained active after a complete VM reboot;
- the portal Unix socket was recreated with the expected ownership and mode;
- `systemctl --failed` reported no failed units.

The final media test used three simultaneous clients:

1. a moderator on a laptop;
2. a first guest in another browser profile;
3. a second guest on a phone using mobile Internet.

All three clients remained connected with working bidirectional audio and
video. Only the JWT moderator received moderator privileges. The third client
forced the conference through Jitsi Videobridge instead of allowing a
two-client peer-to-peer test to conceal media or NAT defects.

## Acceptance decision

The deployed `v0.1.0-rc2` application passed the clean-VM functional and
reboot tests after the required Jitsi Videobridge NAT mapping was configured.

The application behavior was accepted, while the installation documentation
required the corrections recorded by this validation:

- explicit network prerequisites;
- separate public-address and NAT deployment procedures;
- mandatory UDP port 10000 forwarding and JVB static mapping for NAT;
- `systemctl enable --now jitsi-invite.service`;
- Jitsi Videobridge health verification;
- a mandatory three-client acceptance conference.

The validated VM should be retained as an acceptance-test target for future
release candidates before changes are promoted to a production deployment.
