import discord
from redbot.core import commands, Config
from discord.ui import Button, View
from typing import Optional
import asyncio
import re

class MultiRoleView(View):
    def __init__(self, roles: dict, category: str, custom_emojis: dict = None):
        super().__init__(timeout=None)
        custom_emojis = custom_emojis or {}
        
        layouts = {
            "platform": [
                ("pc", "PC", discord.ButtonStyle.secondary, "💻"),
                ("nintendo", "Nintendo", discord.ButtonStyle.danger, "🎮"),
                ("playstation", "PlayStation", discord.ButtonStyle.primary, "🟦"),
                ("xbox", "Xbox", discord.ButtonStyle.success, "🟩")
            ],
            "location": [
                ("europe", "Europe", discord.ButtonStyle.primary, "🇪🇺"),
                ("na", "North America", discord.ButtonStyle.success, "🇺🇸"),
                ("sa", "South America", discord.ButtonStyle.success, "🇧🇷"),
                ("asia", "Asia", discord.ButtonStyle.danger, "🏮"),
                ("oceania", "Oceania", discord.ButtonStyle.primary, "🌊"),
                ("africa", "Africa", discord.ButtonStyle.secondary, "🐘")
            ],
            "pronoun": [
                ("he", "He/Him", discord.ButtonStyle.secondary, "🔹"),
                ("she", "She/Her", discord.ButtonStyle.secondary, "🔸"),
                ("they", "They/Them", discord.ButtonStyle.secondary, "▫️"),
                ("ask", "Other/Ask", discord.ButtonStyle.secondary, "💬")
            ]
        }

        for key, label, style, default_emoji in layouts.get(category, []):
            role_id = roles.get(key)
            if not role_id:
                continue

            emoji_to_use = custom_emojis.get(key) or default_emoji
            # Ensure emoji is valid
            if emoji_to_use and not isinstance(emoji_to_use, str):
                emoji_to_use = None
            
            self.add_item(Button(
                style=style,
                label=label,
                emoji=emoji_to_use,
                custom_id=f"selfrole_{category}_{key}"
            ))

class SelfRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5566778899)
        self.config.register_guild(platforms={}, platform_emojis={}, locations={}, pronouns={})

    async def button_listener(self, interaction: discord.Interaction):
        if not interaction.data or "custom_id" not in interaction.data:
            return
        cid = interaction.data["custom_id"]
        if not cid.startswith("selfrole_"):
            return
            
        parts = cid.split("_")
        cat_map = {"platform": "platforms", "location": "locations", "pronoun": "pronouns"}
        data = await self.config.guild(interaction.guild).get_attr(cat_map[parts[1]])()
        
        role = interaction.guild.get_role(data.get(parts[2]))
        if not role: 
            return
            
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message(f"Removed **{role.name}**.", ephemeral=True)
            else:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"Added **{role.name}**.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to manage roles.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=True)

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def selfroles(self, ctx):
        """Self-assignable roles management"""
        pass

    @selfroles.command()
    async def clear_data(self, ctx):
        await self.config.guild(ctx.guild).clear()
        await ctx.send("✅ Data cleared.")

    @selfroles.command()
    async def setup(self, ctx):
        """Reaction-based setup for perfect custom emojis."""
        setup_structure = {
            "platforms": ["PC", "Nintendo", "PlayStation", "Xbox"],
            "locations": ["Europe", "North America", "South America", "Asia", "Oceania", "Africa"],
            "pronouns": ["He/Him", "She/Her", "They/Them", "Other/Ask"]
        }
        
        await ctx.send("Starting setup. Please respond to each prompt with a custom emoji or type 'skip' to use the default.")

        for category, items in setup_structure.items():
            if category == "platforms":
                category_emojis = {}
                for item in items:
                    key = item.lower().replace(" ", "")
                    if key == "pc":
                        key = "pc"
                    elif key == "playstation":
                        key = "playstation"
                    elif key == "nintendo":
                        key = "nintendo"
                    elif key == "xbox":
                        key = "xbox"
                    else:
                        key = key[:2]  # Fallback to first 2 letters
                    
                    await ctx.send(f"Please provide a custom emoji for **{item}** (or type 'skip' to use default):")
                    try:
                        response = await self.bot.wait_for('message', check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=30.0)
                        if response.content.lower() == 'skip':
                            category_emojis[key] = None
                        else:
                            category_emojis[key] = response.content
                    except asyncio.TimeoutError:
                        await ctx.send(f"Timeout for {item}, using default emoji.")
                        category_emojis[key] = None
                
                await self.config.guild(ctx.guild).platform_emojis.set(category_emojis)
            else:
                # For locations and pronouns, just set defaults for now
                pass
        
        await ctx.send("Setup completed! You can now use the commands to manage self-assignable roles.")

    @selfroles.command()
    async def post(self, ctx, channel: discord.TextChannel = None):
        """Post the self-assignable role buttons in a channel"""
        if channel is None:
            channel = ctx.channel
            
        # Get current data
        platforms = await self.config.guild(ctx.guild).platforms()
        locations = await self.config.guild(ctx.guild).locations()
        pronouns = await self.config.guild(ctx.guild).pronouns()
        platform_emojis = await self.config.guild(ctx.guild).platform_emojis()
        
        if not platforms and not locations and not pronouns:
            await ctx.send("No role data configured. Please run setup first.")
            return
            
        # Send platform buttons
        if platforms:
            platform_view = MultiRoleView(platforms, "platform", platform_emojis)
            await channel.send("Choose your platform:", view=platform_view)
            
        # Send location buttons
        if locations:
            location_view = MultiRoleView(locations, "location")
            await channel.send("Choose your location:", view=location_view)
            
        # Send pronoun buttons
        if pronouns:
            pronoun_view = MultiRoleView(pronouns, "pronoun")
            await channel.send("Choose your pronouns:", view=pronoun_view)
            
        await ctx.send(f"✅ Posted role buttons in {channel.mention}")

    @selfroles.command()
    async def add_platform(self, ctx, platform_name: str, role: discord.Role):
        """Add a platform role"""
        platforms = await self.config.guild(ctx.guild).platforms()
        platforms[platform_name.lower()] = role.id
        await self.config.guild(ctx.guild).platforms.set(platforms)
        await ctx.send(f"✅ Added platform **{platform_name}** with role **{role.name}**")

    @selfroles.command()
    async def add_location(self, ctx, location_name: str, role: discord.Role):
        """Add a location role"""
        locations = await self.config.guild(ctx.guild).locations()
        locations[location_name.lower()] = role.id
        await self.config.guild(ctx.guild).locations.set(locations)
        await ctx.send(f"✅ Added location **{location_name}** with role **{role.name}**")

    @selfroles.command()
    async def add_pronoun(self, ctx, pronoun_name: str, role: discord.Role):
        """Add a pronoun role"""
        pronouns = await self.config.guild(ctx.guild).pronouns()
        pronouns[pronoun_name.lower()] = role.id
        await self.config.guild(ctx.guild).pronouns.set(pronouns)
        await ctx.send(f"✅ Added pronoun **{pronoun_name}** with role **{role.name}**")

    @selfroles.command()
    async def list_roles(self, ctx):
        """List all configured roles"""
        platforms = await self.config.guild(ctx.guild).platforms()
        locations = await self.config.guild(ctx.guild).locations()
        pronouns = await self.config.guild(ctx.guild).pronouns()
        
        embed = discord.Embed(title="Self-Assignable Roles", color=0x00ff00)
        
        if platforms:
            platform_list = "\n".join([f"**{k}**: <@&{v}>" for k, v in platforms.items()])
            embed.add_field(name="Platforms", value=platform_list, inline=False)
            
        if locations:
            location_list = "\n".join([f"**{k}**: <@&{v}>" for k, v in locations.items()])
            embed.add_field(name="Locations", value=location_list, inline=False)
            
        if pronouns:
            pronoun_list = "\n".join([f"**{k}**: <@&{v}>" for k, v in pronouns.items()])
            embed.add_field(name="Pronouns", value=pronoun_list, inline=False)
            
        if not (platforms or locations or pronouns):
            embed.description = "No roles configured yet."
            
        await ctx.send(embed=embed)

    @selfroles.command()
    async def remove_platform(self, ctx, platform_name: str):
        """Remove a platform role"""
        platforms = await self.config.guild(ctx.guild).platforms()
        if platform_name.lower() in platforms:
            del platforms[platform_name.lower()]
            await self.config.guild(ctx.guild).platforms.set(platforms)
            await ctx.send(f"✅ Removed platform **{platform_name}**")
        else:
            await ctx.send(f"❌ Platform **{platform_name}** not found")

    @selfroles.command()
    async def remove_location(self, ctx, location_name: str):
        """Remove a location role"""
        locations = await self.config.guild(ctx.guild).locations()
        if location_name.lower() in locations:
            del locations[location_name.lower()]
            await self.config.guild(ctx.guild).locations.set(locations)
            await ctx.send(f"✅ Removed location **{location_name}**")
        else:
            await ctx.send(f"❌ Location **{location_name}** not found")

    @selfroles.command()
    async def remove_pronoun(self, ctx, pronoun_name: str):
        """Remove a pronoun role"""
        pronouns = await self.config.guild(ctx.guild).pronouns()
        if pronoun_name.lower() in pronouns:
            del pronouns[pronoun_name.lower()]
            await self.config.guild(ctx.guild).pronouns.set(pronouns)
            await ctx.send(f"✅ Removed pronoun **{pronoun_name}**")
        else:
            await ctx.send(f"❌ Pronoun **{pronoun_name}** not found")
