# BattleSnake Teams Bot

A Discord bot that handled team sign-up and organization for **NJIT BattleSnake 2026**, a competitive programming event where teams build their own [Battlesnake](https://play.battlesnake.com/) bots and try to be the last snake alive.

With one slash command, participants create a team. The bot sets up the team's role and private text and voice channels, and keeps a pinned team directory up to date, so staff don't have to create roles and channels by hand.

<!-- TODO: add a screenshot or two: /team create in action, a team's private category, and the #team-directory message. -->

## Features

- **`/team create`**: create a team with up to 3 teammates (soft cap of 4). It validates the team name, requires the `Participant` role, and allows one team per person. It creates:
  - a `Team: <name>` role
  - a private `TEAM • <name>` category, visible only to the team and `Staff`, with `team-chat` and `team-voice` channels
  - a pinned welcome message in the team chat
- **Auto-updating team directory**: a pinned message in `#team-directory` listing every team and its members, rebuilt whenever a team changes.
- **Team-managed channels**: members can add, rename, or delete extra text and voice channels in their own category (up to 10 of each). The base channels are protected.
- **Staff tools**: add members beyond the cap, remove members, rename teams (updates the role and category), and delete teams (cleans up the role, category, and channels).
- **Persistent state**: teams and members are stored in SQLite (`teams.db`), so the bot can restart without losing anything.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `/team create <team_name> [member1] [member2] [member3]` | Participants | Create a team and its role and channels |
| `/team make_text <name>` | Team members | Add a text channel to your team's category |
| `/team make_voice <name>` | Team members | Add a voice channel to your team's category |
| `/team rename_channel <channel> <new_name>` | Team members | Rename one of your team's extra channels |
| `/team delete_channel <channel>` | Team members | Delete one of your team's extra channels |
| `/team add <team_name> <user>` | Staff | Add a user to a team (exceptions to the cap) |
| `/team remove <team_name> <user>` | Staff | Remove a user from a team |
| `/team rename <old_name> <new_name>` | Staff | Rename a team |
| `/team delete <team_name>` | Staff | Delete a team and everything created for it |

## Setup

### 1. Create the Discord application

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application and add a bot.
2. Under **Bot**, enable the **Server Members Intent** (the bot reads members to build the team directory).
3. Invite the bot with the `bot` and `applications.commands` scopes and these permissions: **Manage Roles**, **Manage Channels**, **View Channels**, **Send Messages**, **Read Message History**, and **Manage Messages** (or **Pin Messages**, if your server lists it separately) so it can pin.
4. In your server, move the bot's role **above** the roles it will create, or Discord won't let it assign team roles.

### 2. Prepare the server

Create these before the first run (names must match exactly):

- Roles: `Participant` and `Staff`
- Text channel: `#team-directory` (the bot posts and pins the directory here)
- Optional: `#create-a-ticket`, which is mentioned in each team's welcome message

### 3. Run the bot

Requires a recent Python 3 (3.10+ recommended).

```sh
pip install discord.py python-dotenv
```

Create a `.env` file next to `team_bot.py`:

```env
DISCORD_TOKEN=your-bot-token
GUILD_ID=your-server-id
```

Setting `GUILD_ID` syncs the slash commands to that server instantly. Without it, commands sync globally, which can take up to an hour.

```sh
python team_bot.py
```

The SQLite database (`teams.db`) is created on first run.

## Configuration

Names and limits are constants at the top of `team_bot.py`:

| Setting | Default |
| --- | --- |
| `SOFT_CAP` | 4 members per team |
| `TEAMNAME_MIN` / `TEAMNAME_MAX` | 2–24 characters |
| `ENFORCE_ONE_TEAM_PER_USER` | `True` |
| `MAX_EXTRA_TEXT_CHANNELS` / `MAX_EXTRA_VOICE_CHANNELS` | 10 each |
| `PARTICIPANT_ROLE_NAME` / `STAFF_ROLE_NAME` | `Participant` / `Staff` |
| `TEAM_DIRECTORY_CHANNEL_NAME` | `team-directory` |

## Built with

Python · [discord.py](https://github.com/Rapptz/discord.py) (app commands) · SQLite
