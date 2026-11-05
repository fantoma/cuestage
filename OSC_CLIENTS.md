# OSC clients and master changes

Programs that send OSC commands to CueStage, such as **Script Follower**, normally point at a fixed IP
address: the master's. When CueStage runs with secondary instances (slave or redundancy) and the master goes
down, a secondary can be promoted (by hand or automatically). The promoted instance has a different IP address,
so those programs would keep sending their cues to a machine that is no longer there.

With this small protocol the programs introduce themselves to CueStage, and the new master tells them where to
send their commands. Everything is OSC over UDP: the program talks to CueStage's OSC input port
(Settings › OSC, default 53000), and CueStage answers on a *return port* the program chooses.

## Messages

| Direction | Address | Arguments |
|---|---|---|
| program → CueStage | `/cuestage/client/hello` | `app` (s), `version` (s), `computer name` (s), `return port` (i), `client id` (s), `profile id` (s, optional) |
| CueStage → program | `/cuestage/client/ack` | `instance name` (s), `OSC port` (i), `group members` (s, comma-separated IPs of the secondary instances) |
| new master → program | `/cuestage/master` | `new IP` (s), `OSC port` (i), `instance name` (s), `old master IP` (s, may be empty) |
| program → new master | `/cuestage/client/moved` | `client id` (s), `moved profile ids` (s, comma-separated) |
| program → CueStage | `/cuestage/client/bye` | `client id` (s) |

- The program sends `hello` when it starts and then every few seconds (Script Follower: every 5 s). The `hello`
  also works as a keep-alive: a program that has not been heard from for 30 s is forgotten.
- CueStage answers every `hello` with an `ack` sent to the sender's address and the return port. The status bar
  shows when a new program connects.
- The master passes the list of connected programs to its secondary instances with the state message it already
  sends at least once per second.
- When a secondary is **promoted** (*Leave slave mode*, or automatically when the master is lost), it sends
  `/cuestage/master` to every program the old master knew. The IP is the one this machine uses to reach that
  program, and the OSC port is this instance's own OSC input port. If OSC input is switched off on the
  promoted instance, nothing is sent.
- The program moves its CueStage destinations to the new address, answers `moved`, and from then on sends its
  `hello` messages to the new master, which in turn passes them on to its own secondaries.

## Safety

Anyone on the network can send OSC. A program should apply `/cuestage/master` on its own only when it comes
from a known instance: the old master, or one of the `group members` listed in the last `ack`. In any other
case it should ask the operator. Script Follower ignores the message if it concerns none of its profiles.

## Script Follower

Settings › OSC › **CueStage: master change**. You can switch the feature on or off, choose the return port
(default 53100, or the next free port if that one is busy), and see whether each CueStage profile is connected.
CueStage profiles that pointed to the old master (or to a member of its group) are moved automatically.
Profiles with other plugins (QLab, ChamSys…) are never touched.
