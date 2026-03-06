import os
import re
import sqlite3
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands

from dotenv import load_dotenv
load_dotenv()

# ========== CONFIG ==========
DB_PATH = "teams.db"
TEAM_DIRECTORY_CHANNEL_NAME = "team-directory"
META_DIRECTORY_MESSAGE_ID_KEY = "team_directory_message_id"

# Roles
PARTICIPANT_ROLE_NAME = "Participant"
STAFF_ROLE_NAME = "Staff"

# Naming
TEAM_ROLE_PREFIX = "Team: "
TEAM_CATEGORY_PREFIX = "TEAM • "
TEXT_CHANNEL_NAME = "team-chat"
VOICE_CHANNEL_NAME = "team-voice"

# Limits
SOFT_CAP = 4  # creator + up to 3 others
TEAMNAME_MIN = 2
TEAMNAME_MAX = 24

# Team name allowed characters
TEAMNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]{1,23}$")

# Prevent a user from being on multiple teams
ENFORCE_ONE_TEAM_PER_USER = True

# ========== DB ==========
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            team_name TEXT PRIMARY KEY,
            role_id   INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            text_channel_id INTEGER NOT NULL,
            voice_channel_id INTEGER NOT NULL
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            team_name TEXT NOT NULL,
            user_id   INTEGER NOT NULL,
            PRIMARY KEY (team_name, user_id),
            FOREIGN KEY (team_name) REFERENCES teams(team_name) ON DELETE CASCADE
        );
        """)
        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id);
        """)

def team_exists(team_name: str) -> bool:
    with db() as conn:
        cur = conn.execute("SELECT 1 FROM teams WHERE team_name = ?;", (team_name,))
        return cur.fetchone() is not None

def meta_get(key: str) -> Optional[str]:
    with db() as conn:
        cur = conn.execute("SELECT value FROM meta WHERE key = ?;", (key,))
        row = cur.fetchone()
        return row[0] if row else None

def meta_set(key: str, value: str):
    with db() as conn:
        conn.execute("""
            INSERT INTO meta(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value;
        """, (key, value))

def get_team_by_name(team_name: str):
    with db() as conn:
        cur = conn.execute("""
            SELECT team_name, role_id, category_id, text_channel_id, voice_channel_id
            FROM teams WHERE team_name = ?;
        """, (team_name,))
        return cur.fetchone()

def get_user_team(user_id: int) -> Optional[str]:
    with db() as conn:
        cur = conn.execute("SELECT team_name FROM team_members WHERE user_id = ? LIMIT 1;", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None

def insert_team(team_name: str, role_id: int, category_id: int, text_id: int, voice_id: int, member_ids: List[int]):
    with db() as conn:
        conn.execute("""
            INSERT INTO teams(team_name, role_id, category_id, text_channel_id, voice_channel_id)
            VALUES (?, ?, ?, ?, ?);
        """, (team_name, role_id, category_id, text_id, voice_id))
        conn.executemany("""
            INSERT INTO team_members(team_name, user_id)
            VALUES (?, ?);
        """, [(team_name, uid) for uid in member_ids])

def add_member(team_name: str, user_id: int):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO team_members(team_name, user_id) VALUES (?, ?);", (team_name, user_id))

def remove_member(team_name: str, user_id: int):
    with db() as conn:
        conn.execute("DELETE FROM team_members WHERE team_name = ? AND user_id = ?;", (team_name, user_id))

def rename_team_db(old: str, new: str):
    with db() as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("BEGIN;")
        try:
            # Copy the team row to the new name
            conn.execute("""
                INSERT INTO teams(team_name, role_id, category_id, text_channel_id, voice_channel_id)
                SELECT ?, role_id, category_id, text_channel_id, voice_channel_id
                FROM teams
                WHERE team_name = ?;
            """, (new, old))

            # Move all members to the new team name
            conn.execute("""
                UPDATE team_members
                SET team_name = ?
                WHERE team_name = ?;
            """, (new, old))

            # Delete the old team row
            conn.execute("DELETE FROM teams WHERE team_name = ?;", (old,))

            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

def delete_team_db(team_name: str):
    with db() as conn:
        conn.execute("DELETE FROM teams WHERE team_name = ?;", (team_name,))

def list_members(team_name: str) -> List[int]:
    with db() as conn:
        cur = conn.execute("SELECT user_id FROM team_members WHERE team_name = ?;", (team_name,))
        return [r[0] for r in cur.fetchall()]

# ========== DISCORD BOT ==========
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

GUILD_ID = int(os.getenv("GUILD_ID", "0"))
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

def normalize_teamname(name: str) -> str:
    name = name.strip()
    # Collapse repeated spaces
    name = re.sub(r"\s+", " ", name)
    return name

def validate_teamname(name: str) -> Optional[str]:
    if not (TEAMNAME_MIN <= len(name) <= TEAMNAME_MAX):
        return f"Team name must be {TEAMNAME_MIN}-{TEAMNAME_MAX} characters."
    if not TEAMNAME_RE.match(name):
        return "Team name can only use letters, numbers, spaces, underscores, and dashes (must start with letter/number)."
    return None

def has_role(member: discord.Member, role_name: str) -> bool:
    return any(r.name == role_name for r in member.roles)

async def get_role_by_name(guild: discord.Guild, role_name: str) -> Optional[discord.Role]:
    return discord.utils.get(guild.roles, name=role_name)

def team_role_name(team_name: str) -> str:
    return f"{TEAM_ROLE_PREFIX}{team_name}"

def team_category_name(team_name: str) -> str:
    return f"{TEAM_CATEGORY_PREFIX}{team_name}"

def is_staff(interaction: discord.Interaction) -> bool:
    m = interaction.user
    return isinstance(m, discord.Member) and has_role(m, STAFF_ROLE_NAME)

async def get_member_mention(guild: discord.Guild, user_id: int) -> str:
    m = guild.get_member(user_id)
    if m is None:
        try:
            m = await guild.fetch_member(user_id)
        except Exception:
            m = None
    return m.mention if m else f"<@{user_id}>"

def list_all_teams() -> List[str]:
    with db() as conn:
        cur = conn.execute("SELECT team_name FROM teams ORDER BY LOWER(team_name);")
        return [r[0] for r in cur.fetchall()]

async def rebuild_team_directory(guild: discord.Guild):
    channel = discord.utils.get(guild.text_channels, name=TEAM_DIRECTORY_CHANNEL_NAME)
    if channel is None:
        print(f"[directory] Missing channel #{TEAM_DIRECTORY_CHANNEL_NAME}")
        return

    teams = list_all_teams()

    # Build bulletin text
    lines = []
    lines.append("## 🧾 Team Directory")
    lines.append("This list updates automatically when teams are created/changed.\n")
    if not teams:
        lines.append("_No teams yet. Use `/team create` to make one._")
    else:
        for t in teams:
            member_ids = list_members(t)
            member_mentions = [await get_member_mention(guild, uid) for uid in member_ids]
            lines.append(f"### 🐍 {t}")
            lines.append("**Members:** " + (", ".join(member_mentions) if member_mentions else "_(none)_"))
            lines.append("")

    content = "\n".join(lines)

    
    if len(content) > 1900:
        content = content[:1900] + "\n\n_(Directory too long; ask staff to enable embed paging.)_"

    # Find or create the bulletin message
    msg_id = meta_get(META_DIRECTORY_MESSAGE_ID_KEY)
    msg = None
    if msg_id:
        try:
            msg = await channel.fetch_message(int(msg_id))
        except Exception:
            msg = None

    if msg is None:
        msg = await channel.send(content)
        try:
            await msg.pin(reason="Team directory bulletin")
        except Exception:
            pass
        meta_set(META_DIRECTORY_MESSAGE_ID_KEY, str(msg.id))
    else:
        await msg.edit(content=content)

MAX_EXTRA_TEXT_CHANNELS = 10
MAX_EXTRA_VOICE_CHANNELS = 10

def sanitize_channel_name(name: str) -> Optional[str]:
    name = name.strip().lower()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9\-]", "", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    if len(name) < 2 or len(name) > 32:
        return None
    return name

def is_team_channel_base(name: str) -> bool:
    return name in (TEXT_CHANNEL_NAME, VOICE_CHANNEL_NAME)

@bot.event
async def on_ready():
    init_db()
    print(f"Logged in as {bot.user} (id={bot.user.id})")

    try:
        if GUILD_ID:
            g = bot.get_guild(GUILD_ID)
            if g:
                await rebuild_team_directory(g)

            guild_obj = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild_obj)
            await bot.tree.sync(guild=guild_obj)
            print(f"Synced commands to guild {GUILD_ID}")
        else:
            await bot.tree.sync()
            print("Synced commands globally (may take up to an hour to appear).")
    except discord.Forbidden:
        print("ERROR: Missing Access during command sync. Check GUILD_ID and bot invite scopes.")
    except Exception as e:
        print(f"ERROR syncing commands: {e!r}")

team_group = app_commands.Group(name="team", description="Team management commands")

@team_group.command(name="create", description="Create a team (soft cap 4). Creates role + category + text & voice channels.")
@app_commands.describe(
    team_name="Team name (2-24 chars, letters/numbers/spaces/_/-)",
    member1="Optional teammate",
    member2="Optional teammate",
    member3="Optional teammate"
)
async def team_create(
    interaction: discord.Interaction,
    team_name: str,
    member1: Optional[discord.Member] = None,
    member2: Optional[discord.Member] = None,
    member3: Optional[discord.Member] = None
):
    await interaction.response.defer(ephemeral=True)

    if not isinstance(interaction.user, discord.Member):
        return await interaction.followup.send("This command must be run in a server.", ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    # Participant gating
    if not has_role(interaction.user, PARTICIPANT_ROLE_NAME) and not has_role(interaction.user, STAFF_ROLE_NAME):
        return await interaction.followup.send(
            f"You need the **{PARTICIPANT_ROLE_NAME}** role to create a team.", ephemeral=True
        )

    team_name = normalize_teamname(team_name)
    err = validate_teamname(team_name)
    if err:
        return await interaction.followup.send(err, ephemeral=True)

    if team_exists(team_name):
        return await interaction.followup.send("That team name already exists. Pick a different name.", ephemeral=True)

    members = [interaction.user, member1, member2, member3]
    members = [m for m in members if m is not None]

    # Unique members only
    uniq = {}
    for m in members:
        uniq[m.id] = m
    members = list(uniq.values())

    if len(members) > SOFT_CAP:
        return await interaction.followup.send(
            f"Soft cap is **{SOFT_CAP}** members. Create the team with up to {SOFT_CAP} and open a ticket to add more.",
            ephemeral=True
        )

    # Enforcing one team per user
    if ENFORCE_ONE_TEAM_PER_USER:
        for m in members:
            existing = get_user_team(m.id)
            if existing is not None:
                return await interaction.followup.send(
                    f"{m.mention} is already on team **{existing}**. Remove them first or choose different members.",
                    ephemeral=True
                )

    staff_role = await get_role_by_name(guild, STAFF_ROLE_NAME)
    if staff_role is None:
        return await interaction.followup.send(f"Missing role: **{STAFF_ROLE_NAME}** (create it first).", ephemeral=True)

    # Create role
    role = await guild.create_role(
        name=team_role_name(team_name),
        colour=discord.Colour.gold(),
        reason=f"Team created by {interaction.user} for BattleSnake event"
    )

    # Permission overwrites for category
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            connect=True,
            speak=True
        ),
        staff_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            connect=True,
            speak=True
        )
    }

    category = await guild.create_category(
        name=team_category_name(team_name),
        overwrites=overwrites,
        reason="Auto-created team category"
    )

    text_ch = await guild.create_text_channel(
        name=TEXT_CHANNEL_NAME,
        category=category,
        reason="Auto-created team text channel"
    )

    voice_ch = await guild.create_voice_channel(
        name=VOICE_CHANNEL_NAME,
        category=category,
        reason="Auto-created team voice channel"
    )

    # Assign role to members
    for m in members:
        await m.add_roles(role, reason="Added to team")

    insert_team(
        team_name=team_name,
        role_id=role.id,
        category_id=category.id,
        text_id=text_ch.id,
        voice_id=voice_ch.id,
        member_ids=[m.id for m in members]
    )

    # Pin a starter message in team chat
    try:
        starter = (
            f"Welcome to **{team_name}**!\n\n"
            "• Remember, you can create a support ticket in #create-a-ticket to ask questions if you get stuck\n"
        )
        msg = await text_ch.send(starter)
        await msg.pin(reason="Team starter info")
    except Exception:
        pass

    # Update the directory bulletin
    await rebuild_team_directory(guild)

    await interaction.followup.send(
        f"✅ Team **{team_name}** created.\n"
        f"Role: `{role.name}`\n"
        f"Category: `{category.name}`\n"
        f"Members: " + ", ".join(m.mention for m in members),
        ephemeral=True
    )

@team_group.command(name="add", description="(Staff) Add a user to a team (use for >4 exceptions).")
@app_commands.describe(team_name="Existing team name", user="User to add")
async def team_add(interaction: discord.Interaction, team_name: str, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not is_staff(interaction):
        return await interaction.followup.send("Staff only.", ephemeral=True)

    team_name = normalize_teamname(team_name)
    row = get_team_by_name(team_name)
    if not row:
        return await interaction.followup.send("Team not found.", ephemeral=True)

    if ENFORCE_ONE_TEAM_PER_USER:
        existing = get_user_team(user.id)
        if existing is not None and existing != team_name:
            return await interaction.followup.send(
                f"{user.mention} is already on team **{existing}**.", ephemeral=True
            )

    _, role_id, _, _, _ = row
    role = interaction.guild.get_role(role_id) if interaction.guild else None
    if role is None:
        return await interaction.followup.send("Team role missing (was it deleted?).", ephemeral=True)

    await user.add_roles(role, reason="Staff added member to team")
    add_member(team_name, user.id)
    # Update the directory bulletin
    await rebuild_team_directory(interaction.guild)
    await interaction.followup.send(f"✅ Added {user.mention} to **{team_name}**.", ephemeral=True)

@team_group.command(name="make_text", description="Create a text channel inside your team category.")
@app_commands.describe(name="Channel name (letters/numbers/spaces; will be formatted)")
async def team_make_text(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)

    if not isinstance(interaction.user, discord.Member) or not interaction.guild:
        return await interaction.followup.send("Run this in the server.", ephemeral=True)

    # Find the user's team
    team_name = get_user_team(interaction.user.id)
    if not team_name:
        return await interaction.followup.send("You are not on a team yet.", ephemeral=True)

    row = get_team_by_name(team_name)
    if not row:
        return await interaction.followup.send("Your team is missing from the database (tell staff).", ephemeral=True)

    _, _, category_id, _, _ = row
    category = interaction.guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return await interaction.followup.send("Your team category is missing (tell staff).", ephemeral=True)

    safe = sanitize_channel_name(name)
    if not safe:
        return await interaction.followup.send("Invalid channel name. Try something like `strategy` or `dev-chat`.", ephemeral=True)

    # Limit how many extra text channels can be created
    existing_text = [c for c in category.channels if isinstance(c, discord.TextChannel)]
    # subtract the base team-chat channel
    extra_text_count = max(0, len(existing_text) - 1)
    if extra_text_count >= MAX_EXTRA_TEXT_CHANNELS:
        return await interaction.followup.send(f"Channel limit reached (max {MAX_EXTRA_TEXT_CHANNELS} extra text channels).", ephemeral=True)

    # Prevent duplicates within the category
    if any(getattr(c, "name", "") == safe for c in category.channels):
        return await interaction.followup.send("A channel with that name already exists in your category.", ephemeral=True)

    ch = await interaction.guild.create_text_channel(
        name=safe,
        category=category,
        reason=f"Team member {interaction.user} created a channel for {team_name}"
    )

    await interaction.followup.send(f"✅ Created {ch.mention} in **{team_name}**.", ephemeral=True)

@team_group.command(name="make_voice", description="Create a voice channel inside your team category.")
@app_commands.describe(name="Voice channel name (letters/numbers/spaces; will be formatted)")
async def team_make_voice(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)

    if not isinstance(interaction.user, discord.Member) or not interaction.guild:
        return await interaction.followup.send("Run this in the server.", ephemeral=True)

    team_name = get_user_team(interaction.user.id)
    if not team_name:
        return await interaction.followup.send("You are not on a team yet.", ephemeral=True)

    row = get_team_by_name(team_name)
    if not row:
        return await interaction.followup.send("Your team is missing from the database (tell staff).", ephemeral=True)

    _, _, category_id, _, _ = row
    category = interaction.guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return await interaction.followup.send("Your team category is missing (tell staff).", ephemeral=True)

    safe = sanitize_channel_name(name)
    if not safe:
        return await interaction.followup.send("Invalid name. Try something like `standup` or `scrims`.", ephemeral=True)

    existing_voice = [c for c in category.channels if isinstance(c, discord.VoiceChannel)]
    extra_voice_count = max(0, len(existing_voice) - 1)
    if extra_voice_count >= MAX_EXTRA_VOICE_CHANNELS:
        return await interaction.followup.send(f"Channel limit reached (max {MAX_EXTRA_VOICE_CHANNELS} extra voice channels).", ephemeral=True)

    if any(getattr(c, "name", "") == safe for c in category.channels):
        return await interaction.followup.send("A channel with that name already exists in your category.", ephemeral=True)

    ch = await interaction.guild.create_voice_channel(
        name=safe,
        category=category,
        reason=f"Team member {interaction.user} created a voice channel for {team_name}"
    )

    await interaction.followup.send(f"✅ Created voice channel **{ch.name}** in **{team_name}**.", ephemeral=True)

@team_group.command(name="rename_channel", description="Rename a channel inside your team category.")
@app_commands.describe(channel="Channel to rename", new_name="New name")
async def team_rename_channel(interaction: discord.Interaction, channel: discord.abc.GuildChannel, new_name: str):
    await interaction.response.defer(ephemeral=True)

    if not isinstance(interaction.user, discord.Member) or not interaction.guild:
        return await interaction.followup.send("Run this in the server.", ephemeral=True)

    team_name = get_user_team(interaction.user.id)
    if not team_name:
        return await interaction.followup.send("You are not on a team yet.", ephemeral=True)

    row = get_team_by_name(team_name)
    if not row:
        return await interaction.followup.send("Your team is missing from the database (tell staff).", ephemeral=True)

    _, _, category_id, _, _ = row

    # Must be in their category
    if getattr(channel, "category_id", None) != category_id:
        return await interaction.followup.send("You can only rename channels in your own team category.", ephemeral=True)

    safe = sanitize_channel_name(new_name)
    if not safe:
        return await interaction.followup.send("Invalid new name.", ephemeral=True)

    # Disallow renaming the base channels
    if is_team_channel_base(getattr(channel, "name", "")):
        return await interaction.followup.send("You can’t rename the base team channels.", ephemeral=True)

    await channel.edit(name=safe, reason=f"Team member {interaction.user} renamed a channel for {team_name}")
    await interaction.followup.send(f"✅ Renamed channel to **{safe}**.", ephemeral=True)

@team_group.command(name="delete_channel", description="Delete a channel inside your team category.")
@app_commands.describe(channel="Channel to delete")
async def team_delete_channel(interaction: discord.Interaction, channel: discord.abc.GuildChannel):
    await interaction.response.defer(ephemeral=True)

    if not isinstance(interaction.user, discord.Member) or not interaction.guild:
        return await interaction.followup.send("Run this in the server.", ephemeral=True)

    team_name = get_user_team(interaction.user.id)
    if not team_name:
        return await interaction.followup.send("You are not on a team yet.", ephemeral=True)

    row = get_team_by_name(team_name)
    if not row:
        return await interaction.followup.send("Your team is missing from the database (tell staff).", ephemeral=True)

    _, _, category_id, _, _ = row

    if getattr(channel, "category_id", None) != category_id:
        return await interaction.followup.send("You can only delete channels in your own team category.", ephemeral=True)

    if is_team_channel_base(getattr(channel, "name", "")):
        return await interaction.followup.send("You can’t delete the base team channels.", ephemeral=True)

    await channel.delete(reason=f"Team member {interaction.user} deleted a channel for {team_name}")
    await interaction.followup.send("✅ Channel deleted.", ephemeral=True)

@team_group.command(name="remove", description="(Staff) Remove a user from a team.")
@app_commands.describe(team_name="Existing team name", user="User to remove")
async def team_remove(interaction: discord.Interaction, team_name: str, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not is_staff(interaction):
        return await interaction.followup.send("Staff only.", ephemeral=True)

    team_name = normalize_teamname(team_name)
    row = get_team_by_name(team_name)
    if not row:
        return await interaction.followup.send("Team not found.", ephemeral=True)

    _, role_id, _, _, _ = row
    role = interaction.guild.get_role(role_id) if interaction.guild else None
    if role is None:
        return await interaction.followup.send("Team role missing (was it deleted?).", ephemeral=True)

    await user.remove_roles(role, reason="Staff removed member from team")
    remove_member(team_name, user.id)
    # Update the directory bulletin
    await rebuild_team_directory(interaction.guild)
    await interaction.followup.send(f"✅ Removed {user.mention} from **{team_name}**.", ephemeral=True)

@team_group.command(name="rename", description="(Staff) Rename a team (updates role + category name).")
@app_commands.describe(old_name="Existing team name", new_name="New team name")
async def team_rename(interaction: discord.Interaction, old_name: str, new_name: str):
    await interaction.response.defer(ephemeral=True)
    if not is_staff(interaction):
        return await interaction.followup.send("Staff only.", ephemeral=True)

    old_name = normalize_teamname(old_name)
    new_name = normalize_teamname(new_name)

    err = validate_teamname(new_name)
    if err:
        return await interaction.followup.send(err, ephemeral=True)

    if team_exists(new_name):
        return await interaction.followup.send("That new team name already exists.", ephemeral=True)

    row = get_team_by_name(old_name)
    if not row:
        return await interaction.followup.send("Old team not found.", ephemeral=True)

    _, role_id, category_id, _, _ = row
    guild = interaction.guild
    assert guild is not None

    role = guild.get_role(role_id)
    category = guild.get_channel(category_id)

    if role:
        await role.edit(name=team_role_name(new_name), reason="Team rename")
    if isinstance(category, discord.CategoryChannel):
        await category.edit(name=team_category_name(new_name), reason="Team rename")

    rename_team_db(old_name, new_name)
    # Update the directory bulletin
    await rebuild_team_directory(guild)
    await interaction.followup.send(f"✅ Renamed **{old_name}** → **{new_name}**.", ephemeral=True)

@team_group.command(name="delete", description="(Staff) Delete a team (role + category + channels).")
@app_commands.describe(team_name="Team name to delete")
async def team_delete(interaction: discord.Interaction, team_name: str):
    await interaction.response.defer(ephemeral=True)
    if not is_staff(interaction):
        return await interaction.followup.send("Staff only.", ephemeral=True)

    team_name = normalize_teamname(team_name)
    row = get_team_by_name(team_name)
    if not row:
        return await interaction.followup.send("Team not found.", ephemeral=True)

    status_msg = await interaction.followup.send(f"🧹 Deleting **{team_name}**…", ephemeral=True)

    _, role_id, category_id, _, _ = row
    guild = interaction.guild
    assert guild is not None

    role = guild.get_role(role_id)
    category = guild.get_channel(category_id)

    # Delete channels/category
    if isinstance(category, discord.CategoryChannel):
        for ch in list(category.channels):
            try:
                await ch.delete(reason="Team deleted")
            except Exception:
                pass
        try:
            await category.delete(reason="Team deleted")
        except Exception:
            pass

    # Delete role
    if role:
        try:
            await role.delete(reason="Team deleted")
        except Exception:
            pass

    delete_team_db(team_name)

    # Update the directory bulletin
    await rebuild_team_directory(guild)

    try:
        await status_msg.edit(content=f"✅ Deleted team **{team_name}**.")
    except Exception:
        try:
            await interaction.followup.send(f"✅ Deleted team **{team_name}**.", ephemeral=True)
        except Exception:
            pass

bot.tree.add_command(team_group)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("Missing DISCORD_TOKEN env var.")
    bot.run(DISCORD_TOKEN)