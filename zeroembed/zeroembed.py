from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import pagify
import discord
from typing import Optional
import asyncio
import json
from datetime import datetime

class ZeroEmbed(commands.Cog):
    """Manage and send custom embeds"""
    
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "stored_embeds": {},
            "message_links": {}  # Store message IDs for updating embeds
        }
        self.config.register_guild(**default_guild)

    @commands.group()
    @commands.admin_or_permissions(manage_messages=True)
    async def zeroembed(self, ctx):
        """Manage custom embeds"""
        pass

    @zeroembed.command()
    async def create(self, ctx, name: str):
        """Create a new embed"""
        async with self.config.guild(ctx.guild).stored_embeds() as stored_embeds:
            if name in stored_embeds:
                await ctx.send("An embed with that name already exists!")
                return
            
            stored_embeds[name] = {
                "title": "",
                "description": "",
                "fields": [],
                "color": 0,
                "footer": "",
                "thumbnail": "",
                "image": ""
            }
            
        await ctx.send(f"Created new embed: {name}")

    @zeroembed.command()
    async def send(self, ctx, name: str, channel: Optional[discord.TextChannel] = None):
        """Send a stored embed to a channel"""
        if channel is None:
            channel = ctx.channel
            
        stored_embeds = await self.config.guild(ctx.guild).stored_embeds()
        
        if name not in stored_embeds:
            await ctx.send("That embed doesn't exist!")
            return
            
        embed_data = stored_embeds[name].copy()
        
        # Process variables
        embed_data["title"] = await self._process_variables(embed_data["title"], ctx)
        embed_data["description"] = await self._process_variables(embed_data["description"], ctx)
        embed_data["footer"] = await self._process_variables(embed_data["footer"], ctx)
        
        for field in embed_data["fields"]:
            field["name"] = await self._process_variables(field["name"], ctx)
            field["value"] = await self._process_variables(field["value"], ctx)
            
        embed = await self._create_embed(embed_data)
        message = await channel.send(embed=embed)
        
        # Store message link for updating
        async with self.config.guild(ctx.guild).message_links() as message_links:
            message_links[str(message.id)] = name

    @zeroembed.command()
    async def edit(self, ctx, name: str, field: str, *, value: str):
        """Edit an embed field (title/description/color/footer/thumbnail/image)"""
        async with self.config.guild(ctx.guild).stored_embeds() as stored_embeds:
            if name not in stored_embeds:
                await ctx.send("That embed doesn't exist!")
                return
                
            if field.lower() not in ["title", "description", "color", "footer", "thumbnail", "image"]:
                await ctx.send("Invalid field! Valid fields are: title, description, color, footer, thumbnail, image")
                return
                
            if field.lower() == "color":
                try:
                    value = int(value.strip("#"), 16)
                except ValueError:
                    await ctx.send("Invalid color hex code!")
                    return
                    
            stored_embeds[name][field.lower()] = value
            
        await ctx.send(f"Updated {field} for embed: {name}")

    @zeroembed.command()
    async def addfield(self, ctx, name: str, field_name: str, *, field_value: str):
        """Add a field to an embed"""
        async with self.config.guild(ctx.guild).stored_embeds() as stored_embeds:
            if name not in stored_embeds:
                await ctx.send("That embed doesn't exist!")
                return
                
            stored_embeds[name]["fields"].append({
                "name": field_name,
                "value": field_value,
                "inline": True
            })
            
        await ctx.send(f"Added field to embed: {name}")

    @zeroembed.command()
    async def removefield(self, ctx, name: str, field_index: int):
        """Remove a field from an embed by its index"""
        async with self.config.guild(ctx.guild).stored_embeds() as stored_embeds:
            if name not in stored_embeds:
                await ctx.send("That embed doesn't exist!")
                return
                
            if field_index < 0 or field_index >= len(stored_embeds[name]["fields"]):
                await ctx.send("Invalid field index!")
                return
                
            stored_embeds[name]["fields"].pop(field_index)
            
        await ctx.send(f"Removed field from embed: {name}")

    @zeroembed.command()
    async def list(self, ctx):
        """List all stored embeds"""
        stored_embeds = await self.config.guild(ctx.guild).stored_embeds()
        if not stored_embeds:
            await ctx.send("No embeds stored!")
            return
            
        embed_list = "\n".join(stored_embeds.keys())
        await ctx.send(f"Stored embeds:\n```\n{embed_list}\n```")

    @zeroembed.command()
    async def delete(self, ctx, name: str):
        """Delete a stored embed"""
        async with self.config.guild(ctx.guild).stored_embeds() as stored_embeds:
            if name not in stored_embeds:
                await ctx.send("That embed doesn't exist!")
                return
                
            del stored_embeds[name]
            
        await ctx.send(f"Deleted embed: {name}")

    @zeroembed.command()
    async def importjson(self, ctx, name: str, *, json_data: str):
        """Import an embed from JSON format"""
        try:
            embed_data = json.loads(json_data)
            
            # Validate the JSON structure
            required_keys = ["title", "description", "fields", "color", "footer", "thumbnail", "image"]
            for key in required_keys:
                if key not in embed_data:
                    embed_data[key] = "" if key != "fields" else []
                if key == "color" and embed_data[key] == "":
                    embed_data[key] = 0
                    
            # Validate fields structure
            for field in embed_data["fields"]:
                if not all(k in field for k in ("name", "value", "inline")):
                    await ctx.send("Invalid field structure in JSON!")
                    return
                    
            async with self.config.guild(ctx.guild).stored_embeds() as stored_embeds:
                stored_embeds[name] = embed_data
                
            await ctx.send(f"Successfully imported embed: {name}")
            
        except json.JSONDecodeError:
            await ctx.send("Invalid JSON format!")
            return

    @zeroembed.command()
    async def exportjson(self, ctx, name: str):
        """Export an embed as JSON"""
        stored_embeds = await self.config.guild(ctx.guild).stored_embeds()
        
        if name not in stored_embeds:
            await ctx.send("That embed doesn't exist!")
            return
            
        embed_data = stored_embeds[name]
        json_data = json.dumps(embed_data, indent=4)
        
        # Split into chunks if too long
        for page in pagify(json_data, delims=["\n"], page_length=1990):
            await ctx.send(f"```json\n{page}\n```")

    @zeroembed.command()
    async def importfile(self, ctx, name: str):
        """Import an embed from a JSON file (attach the file with the command)"""
        if not ctx.message.attachments:
            await ctx.send("Please attach a JSON file!")
            return
            
        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith('.json'):
            await ctx.send("Please attach a JSON file!")
            return
            
        try:
            json_content = await attachment.read()
            json_data = json.loads(json_content)
            
            # Validate the JSON structure
            required_keys = ["title", "description", "fields", "color", "footer", "thumbnail", "image"]
            for key in required_keys:
                if key not in json_data:
                    json_data[key] = "" if key != "fields" else []
                if key == "color" and json_data[key] == "":
                    json_data[key] = 0
                    
            # Validate fields structure
            for field in json_data["fields"]:
                if not all(k in field for k in ("name", "value", "inline")):
                    await ctx.send("Invalid field structure in JSON!")
                    return
                    
            async with self.config.guild(ctx.guild).stored_embeds() as stored_embeds:
                stored_embeds[name] = json_data
                
            await ctx.send(f"Successfully imported embed from file: {name}")
            
        except json.JSONDecodeError:
            await ctx.send("Invalid JSON format in file!")
            return
        except Exception as e:
            await ctx.send(f"Error reading file: {str(e)}")
            return

    @zeroembed.command()
    async def preview(self, ctx, name: str):
        """Preview how an embed will look without sending it to a channel"""
        stored_embeds = await self.config.guild(ctx.guild).stored_embeds()
        
        if name not in stored_embeds:
            await ctx.send("That embed doesn't exist!")
            return
            
        embed_data = stored_embeds[name]
        embed = await self._create_embed(embed_data)
        await ctx.send(f"Preview of embed '{name}':", embed=embed)

    @zeroembed.command()
    async def duplicate(self, ctx, source_name: str, new_name: str):
        """Duplicate an existing embed with a new name"""
        async with self.config.guild(ctx.guild).stored_embeds() as stored_embeds:
            if source_name not in stored_embeds:
                await ctx.send("Source embed doesn't exist!")
                return
                
            if new_name in stored_embeds:
                await ctx.send("An embed with that name already exists!")
                return
                
            stored_embeds[new_name] = stored_embeds[source_name].copy()
            
        await ctx.send(f"Created duplicate embed: {new_name}")

    @zeroembed.command()
    async def info(self, ctx, name: str):
        """Get detailed information about an embed"""
        stored_embeds = await self.config.guild(ctx.guild).stored_embeds()
        message_links = await self.config.guild(ctx.guild).message_links()
        
        if name not in stored_embeds:
            await ctx.send("That embed doesn't exist!")
            return
            
        embed_data = stored_embeds[name]
        
        # Create info embed
        info = discord.Embed(title=f"Embed Info: {name}", color=discord.Color.blue())
        info.add_field(name="Title Length", value=f"{len(embed_data['title'])}/256 characters", inline=True)
        info.add_field(name="Description Length", value=f"{len(embed_data['description'])}/4096 characters", inline=True)
        info.add_field(name="Fields", value=f"{len(embed_data['fields'])}/25 fields", inline=True)
        
        # Add active message links
        active_messages = [k for k, v in message_links.items() if v == name]
        if active_messages:
            info.add_field(name="Active Messages", value=len(active_messages), inline=False)
            
        await ctx.send(embed=info)

    @zeroembed.command()
    async def update(self, ctx, name: str):
        """Update all active messages using this embed"""
        stored_embeds = await self.config.guild(ctx.guild).stored_embeds()
        message_links = await self.config.guild(ctx.guild).message_links()
        
        if name not in stored_embeds:
            await ctx.send("That embed doesn't exist!")
            return
            
        embed_data = stored_embeds[name]
        embed = await self._create_embed(embed_data)
        
        updated = 0
        failed = 0
        
        for message_id, embed_name in message_links.items():
            if embed_name != name:
                continue
                
            try:
                # Find message across all channels
                message = None
                for channel in ctx.guild.channels:
                    if isinstance(channel, discord.TextChannel):
                        try:
                            message = await channel.fetch_message(int(message_id))
                            if message:
                                break
                        except:
                            continue
                
                if message:
                    await message.edit(embed=embed)
                    updated += 1
                else:
                    failed += 1
                    
            except:
                failed += 1
                
        await ctx.send(f"Updated {updated} messages. Failed to update {failed} messages.")

    @zeroembed.command()
    async def variables(self, ctx):
        """Show available variables for dynamic embed content"""
        variables = discord.Embed(title="Available Variables", color=discord.Color.blue())
        variables.description = """
You can use these variables in your embeds:
{server} - Server name
{member_count} - Total members
{channel} - Channel name
{date} - Current date
{time} - Current time
{user} - Member who triggered the embed
"""
        await ctx.send(embed=variables)

    async def _create_embed(self, embed_data: dict) -> discord.Embed:
        """Helper function to create embed from data"""
        embed = discord.Embed(
            title=embed_data["title"],
            description=embed_data["description"],
            color=embed_data["color"]
        )
        
        if embed_data["thumbnail"]:
            embed.set_thumbnail(url=embed_data["thumbnail"])
        if embed_data["image"]:
            embed.set_image(url=embed_data["image"])
        if embed_data["footer"]:
            embed.set_footer(text=embed_data["footer"])
            
        for field in embed_data["fields"]:
            embed.add_field(
                name=field["name"],
                value=field["value"],
                inline=field.get("inline", True)
            )
            
        return embed

    async def _process_variables(self, text: str, ctx) -> str:
        """Process variables in embed text"""
        if not text:
            return text
            
        replacements = {
            "{server}": ctx.guild.name,
            "{member_count}": str(ctx.guild.member_count),
            "{channel}": ctx.channel.name,
            "{date}": datetime.now().strftime("%Y-%m-%d"),
            "{time}": datetime.now().strftime("%H:%M:%S"),
            "{user}": ctx.author.name
        }
        
        for key, value in replacements.items():
            text = text.replace(key, value)
            
        return text

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """Remove message links when embed messages are deleted"""
        async with self.config.guild(message.guild).message_links() as message_links:
            if str(message.id) in message_links:
                del message_links[str(message.id)]

def setup(bot):
    bot.add_cog(ZeroEmbed(bot))
