import asyncio
import discord
import logging
from typing import Dict, Optional, List, Tuple, Union
from redbot.core import commands
from redbot.core.utils.chat_formatting import pagify

log = logging.getLogger("red.Elkz.zerolivesleft.gamertags")

class GamertagsLogic:
    """Logic for managing user gaming platform usernames"""

    # Platform definitions with emoji and display names
    PLATFORMS = {
        'psn': {'name': 'PlayStation Network', 'emoji': '🎮', 'field': 'PlayStation ID'},
        'xbox': {'name': 'Xbox Live', 'emoji': '🎯', 'field': 'Xbox Gamertag'},
        'steam': {'name': 'Steam', 'emoji': '🚂', 'field': 'Steam Username'},
        'nintendo': {'name': 'Nintendo Switch', 'emoji': '🔴', 'field': 'Nintendo ID'},
        'epic': {'name': 'Epic Games', 'emoji': '⚡', 'field': 'Epic Username'},
        'battlenet': {'name': 'Battle.net', 'emoji': '⚔️', 'field': 'Battle.net ID'},
        'origin': {'name': 'Origin/EA', 'emoji': '🔶', 'field': 'Origin Username'},
        'uplay': {'name': 'Ubisoft Connect', 'emoji': '🎪', 'field': 'Ubisoft Username'},
        'twitch': {'name': 'Twitch', 'emoji': '💜', 'field': 'Twitch Username'}
    }

    def __init__(self, parent_cog):
        self.cog = parent_cog
        self.bot = parent_cog.bot
        self.config = parent_cog.config

        # Register default user config for gamertags
        default_user = {
            "gamertags": {}  # Will store platform: username pairs
        }
        self.config.register_user(**default_user)

    async def find_user(self, ctx: commands.Context, user_input: str) -> Optional[Union[discord.User, discord.Member]]:
        """
        Enhanced user finding that handles various input formats:
        - User mentions (@user)
        - User IDs (123456789)
        - Usernames (partial matching)
        - Display names (partial matching)
        - Case-insensitive matching
        """
        
        # First try the built-in converter (handles mentions, IDs, exact usernames)
        try:
            # Use MemberConverter if in a guild, UserConverter otherwise
            if ctx.guild:
                converter = commands.MemberConverter()
                return await converter.convert(ctx, user_input)
            else:
                converter = commands.UserConverter()
                return await converter.convert(ctx, user_input)
        except commands.BadArgument:
            pass  # Continue with manual search
        
        # Manual search for partial matches
        user_input_lower = user_input.lower()
        
        # Search in guild members first (if in a guild)
        if ctx.guild:
            # Exact matches first
            for member in ctx.guild.members:
                if (member.name.lower() == user_input_lower or 
                    member.display_name.lower() == user_input_lower):
                    return member
            
            # Partial matches
            matches = []
            for member in ctx.guild.members:
                if (user_input_lower in member.name.lower() or 
                    user_input_lower in member.display_name.lower()):
                    matches.append(member)
            
            # If we have matches, return the best one or ask for clarification
            if len(matches) == 1:
                return matches[0]
            elif len(matches) > 1:
                # Multiple matches found - let user choose
                await self._handle_multiple_matches(ctx, matches, user_input)
                return None
        
        # If no guild or no matches in guild, search bot's users
        # This is limited but we can try cached users
        for user in self.bot.users:
            if user.name.lower() == user_input_lower:
                return user
        
        # Partial match in bot users
        partial_matches = []
        for user in self.bot.users:
            if user_input_lower in user.name.lower():
                partial_matches.append(user)
        
        if len(partial_matches) == 1:
            return partial_matches[0]
        elif len(partial_matches) > 1:
            await self._handle_multiple_matches(ctx, partial_matches, user_input)
            return None
        
        return None

    async def _handle_multiple_matches(self, ctx: commands.Context, matches: List[Union[discord.User, discord.Member]], user_input: str):
        """Handle when multiple users match the input"""
        embed = discord.Embed(
            title="🔍 Multiple Users Found",
            description=f"Multiple users match `{user_input}`. Please be more specific or use one of these:",
            color=discord.Color.orange()
        )
        
        match_list = []
        for i, user in enumerate(matches[:10], 1):  # Limit to 10 matches
            if hasattr(user, 'display_name'):  # Member
                match_list.append(f"{i}. **{user.display_name}** ({user.name}) - ID: {user.id}")
            else:  # User
                match_list.append(f"{i}. **{user.name}** - ID: {user.id}")
        
        embed.add_field(
            name="Matches",
            value="\n".join(match_list),
            inline=False
        )
        
        embed.set_footer(text="💡 Tip: You can use @mention, user ID, or be more specific with the name")
        await ctx.send(embed=embed)

    async def setup_gamertags(self, ctx: commands.Context):
        """Start the DM-based gamertag setup process"""
        user = ctx.author

        try:
            # Send initial DM with platform selection instructions
            setup_embed = discord.Embed(
                title="🎮 Gamertag Setup",
                description="Please select the platforms you want to set up by sending a message with the platform names or numbers separated by spaces.\n\n" +
                            "\n".join([f"{i+1}. {info['name']} ({key})" for i, (key, info) in enumerate(self.PLATFORMS.items())]),
                color=discord.Color.blue()
            )
            setup_embed.set_footer(text="For example: '1 3 5' or 'psn steam epic'")
            await user.send(embed=setup_embed)

            # Confirm in guild that DM was sent
            await ctx.send(f"✅ {user.mention}, I've sent you a DM to set up your gamertags!")

        except discord.Forbidden:
            return await ctx.send(
                f"❌ {user.mention}, I couldn't send you a DM! Please enable DMs from server members and try again."
            )

        # Wait for user to respond with platform selections
        def check(m):
            return m.author == user and isinstance(m.channel, discord.DMChannel)

        try:
            message = await self.bot.wait_for('message', check=check, timeout=300)
            user_selections = message.content.strip().lower().split()

            # Map user selections to platform keys
            selected_platforms = []
            for selection in user_selections:
                if selection in self.PLATFORMS:
                    selected_platforms.append(selection)
                elif selection.isdigit() and int(selection) <= len(self.PLATFORMS):
                    platform_keys = list(self.PLATFORMS.keys())
                    selected_platforms.append(platform_keys[int(selection) - 1])

            # Run setup for selected platforms
            await self._run_gamertag_setup(user, selected_platforms)

        except asyncio.TimeoutError:
            timeout_embed = discord.Embed(
                title="⏰ Setup Timed Out",
                description="You took too long to respond. Use the command again to restart the setup process.",
                color=discord.Color.orange()
            )
            await user.send(embed=timeout_embed)

    async def _run_gamertag_setup(self, user: discord.User, selected_platforms: List[str]):
        """Run the interactive gamertag setup process in DMs for selected platforms"""
        gamertags = {}

        for platform_key in selected_platforms:
            platform_info = self.PLATFORMS[platform_key]
            # Create platform prompt embed
            prompt_embed = discord.Embed(
                title=f"{platform_info['emoji']} {platform_info['name']}",
                description=f"What's your **{platform_info['field']}**?\n\n"
                            f"Type your username/ID, or:\n"
                            f"• `skip` to skip this platform\n"
                            f"• `cancel` to stop setup",
                color=discord.Color.green()
            )
            await user.send(embed=prompt_embed)

            # Wait for user response
            def check(m):
                return m.author == user and isinstance(m.channel, discord.DMChannel)

            try:
                message = await self.bot.wait_for('message', check=check, timeout=300)  # 5 minute timeout
                response = message.content.strip()

                if response.lower() == 'cancel':
                    cancel_embed = discord.Embed(
                        title="❌ Setup Cancelled",
                        description="Your gamertag setup has been cancelled.\n"
                                    "No changes were saved.",
                        color=discord.Color.red()
                    )
                    await user.send(embed=cancel_embed)
                    return

                elif response.lower() == 'skip':
                    continue  # Skip this platform

                else:
                    # Validate and save the gamertag
                    if len(response) > 50:
                        await user.send("⚠️ Username too long (max 50 characters). Please try again.")
                        continue

                    gamertags[platform_key] = response

                    # Confirmation
                    confirm_embed = discord.Embed(
                        title="✅ Saved!",
                        description=f"**{platform_info['name']}:** {response}",
                        color=discord.Color.green()
                    )
                    await user.send(embed=confirm_embed)
                    await asyncio.sleep(1)  # Brief pause between questions

            except asyncio.TimeoutError:
                timeout_embed = discord.Embed(
                    title="⏰ Setup Timed Out",
                    description="Setup took too long and was cancelled.\n"
                                "Use `!gtag setup` to try again.",
                    color=discord.Color.orange()
                )
                await user.send(embed=timeout_embed)
                return

        # Save all gamertags to config
        if gamertags:
            await self.config.user(user).gamertags.set(gamertags)

            # Send completion summary
            summary_embed = discord.Embed(
                title="🎉 Gamertag Setup Complete!",
                description=f"Successfully saved **{len(gamertags)}** gamertags!\n\n"
                            "**What's next?**\n"
                            "• Others can view your gamertags with `!gtag @username`\n"
                            "• Update anytime with `!gtag setup`\n"
                            "• Remove all with `!gtag clear`",
                color=discord.Color.gold()
            )

            # Add saved platforms to embed
            saved_platforms = []
            for platform_key in gamertags.keys():
                platform_info = self.PLATFORMS[platform_key]
                saved_platforms.append(f"{platform_info['emoji']} {platform_info['name']}")

            if saved_platforms:
                summary_embed.add_field(
                    name="Saved Platforms",
                    value="\n".join(saved_platforms),
                    inline=False
                )

            await user.send(embed=summary_embed)

        else:
            # No gamertags were saved
            empty_embed = discord.Embed(
                title="📝 Setup Complete",
                description="No gamertags were saved (all platforms were skipped).\n"
                            "Use `!gtag setup` anytime to add your gamertags!",
                color=discord.Color.blue()
            )
            await user.send(embed=empty_embed)

    async def view_gamertags(self, ctx: commands.Context, user_input: str = None):
        """Display a user's gamertags privately via DM"""
        requester = ctx.author
        
        # If no user specified, show their own gamertags
        if not user_input:
            target_user = requester
        else:
            # Find the target user using enhanced search
            target_user = await self.find_user(ctx, user_input)
            if not target_user:
                error_embed = discord.Embed(
                    title="❌ User Not Found",
                    description=f"Could not find a user matching `{user_input}`.\n\n"
                                "**Valid formats:**\n"
                                "• @mention the user\n"
                                "• Use their User ID\n"
                                "• Type their username or display name\n"
                                "• Use partial names (if unique)",
                    color=discord.Color.red()
                )
                error_embed.set_footer(text="💡 Try being more specific if you got multiple matches")
                await ctx.send(embed=error_embed)
                return

        # Get target user's gamertags
        user_gamertags = await self.config.user(target_user).gamertags()

        if not user_gamertags:
            # No gamertags found
            no_tags_embed = discord.Embed(
                title="❌ No Gamertags Found",
                description=f"**{target_user.display_name}** hasn't set up any gamertags yet.\n\n"
                            f"They can use `!gtag setup` to add their gaming usernames!",
                color=discord.Color.red()
            )

            # Try to send via DM first, fallback to channel
            try:
                await requester.send(embed=no_tags_embed)
                if ctx.guild:  # Only show confirmation if in a guild
                    await ctx.send(f"📬 {requester.mention}, I've sent you a DM!")
            except discord.Forbidden:
                await ctx.send(embed=no_tags_embed)
            return

        # Create gamertags display embed
        gamertags_embed = discord.Embed(
            title=f"🎮 {target_user.display_name}'s Gamertags",
            description=f"Gaming platform usernames for **{target_user.display_name}**",
            color=discord.Color.blue()
        )

        # Add user avatar if available
        if target_user.avatar:
            gamertags_embed.set_thumbnail(url=target_user.avatar.url)

        # Add platforms to embed
        platforms = []
        for platform_key, username in user_gamertags.items():
            if platform_key in self.PLATFORMS:
                platform_info = self.PLATFORMS[platform_key]
                platforms.append(f"{platform_info['emoji']} **{platform_info['name']}**\n`{username}`")

        if platforms:
            gamertags_embed.add_field(
                name="🎮 Platforms",
                value="\n\n".join(platforms),
                inline=False
            )

        gamertags_embed.set_footer(
            text=f"Requested by {requester.display_name} • Use !gtag setup to add your own",
            icon_url=requester.avatar.url if requester.avatar else None
        )

        # Try to send via DM first, fallback to channel
        try:
            await requester.send(embed=gamertags_embed)
            if ctx.guild:  # Only show confirmation if in a guild
                await ctx.send(f"📬 {requester.mention}, I've sent you {target_user.display_name}'s gamertags via DM!")
        except discord.Forbidden:
            # DM failed, send in channel but make it less obvious
            gamertags_embed.description = f"*(DM failed - showing here instead)*\n\n{gamertags_embed.description}"
            await ctx.send(embed=gamertags_embed)

    async def clear_gamertags(self, ctx: commands.Context):
        """Clear all of a user's gamertags"""
        user = ctx.author
        user_gamertags = await self.config.user(user).gamertags()

        if not user_gamertags:
            await ctx.send("❌ You don't have any gamertags set up!")
            return

        # Confirmation embed
        confirm_embed = discord.Embed(
            title="⚠️ Clear All Gamertags?",
            description=f"This will permanently delete **{len(user_gamertags)}** saved gamertags.\n\n"
                        "React with ✅ to confirm or ❌ to cancel.",
            color=discord.Color.orange()
        )

        confirm_msg = await ctx.send(embed=confirm_embed)
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, reaction_user):
            return (reaction_user == user and
                    str(reaction.emoji) in ["✅", "❌"] and
                    reaction.message.id == confirm_msg.id)

        try:
            reaction, _ = await self.bot.wait_for('reaction_add', check=check, timeout=30)

            if str(reaction.emoji) == "✅":
                # Clear gamertags
                await self.config.user(user).gamertags.clear()

                success_embed = discord.Embed(
                    title="🗑️ Gamertags Cleared",
                    description="All your gamertags have been successfully deleted!",
                    color=discord.Color.green()
                )
                await ctx.send(embed=success_embed)
            else:
                # Cancelled
                cancel_embed = discord.Embed(
                    title="❌ Cancelled",
                    description="Your gamertags were not deleted.",
                    color=discord.Color.blue()
                )
                await ctx.send(embed=cancel_embed)

        except asyncio.TimeoutError:
            timeout_embed = discord.Embed(
                title="⏰ Timed Out",
                description="Confirmation timed out. Your gamertags were not deleted.",
                color=discord.Color.grey()
            )
            await ctx.send(embed=timeout_embed)

        finally:
            try:
                await confirm_msg.clear_reactions()
            except:
                pass

    async def list_my_gamertags(self, ctx: commands.Context):
        """Show the user their own gamertags"""
        await self.view_gamertags(ctx)  # No user input = show own gamertags

    async def get_stats(self, ctx: commands.Context):
        """Show gamertag system statistics"""
        all_users = await self.config.all_users()

        total_users = len([u for u in all_users.values() if u.get('gamertags')])
        total_gamertags = sum(len(u.get('gamertags', {})) for u in all_users.values())

        # Count platform popularity
        platform_counts = {}
        for user_data in all_users.values():
            for platform in user_data.get('gamertags', {}):
                platform_counts[platform] = platform_counts.get(platform, 0) + 1

        # Create stats embed
        stats_embed = discord.Embed(
            title="📊 Gamertag System Statistics",
            color=discord.Color.blue()
        )

        stats_embed.add_field(
            name="📈 Usage Stats",
            value=f"**Users with gamertags:** {total_users}\n"
                  f"**Total gamertags stored:** {total_gamertags}",
            inline=False
        )

        if platform_counts:
            # Show top 5 most popular platforms
            sorted_platforms = sorted(platform_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            popular_text = []

            for platform_key, count in sorted_platforms:
                if platform_key in self.PLATFORMS:
                    platform_info = self.PLATFORMS[platform_key]
                    popular_text.append(f"{platform_info['emoji']} **{platform_info['name']}:** {count} users")

            if popular_text:
                stats_embed.add_field(
                    name="🏆 Most Popular Platforms",
                    value="\n".join(popular_text),
                    inline=False
                )

        stats_embed.set_footer(text="Use !gtag setup to add your gamertags!")
        await ctx.send(embed=stats_embed)