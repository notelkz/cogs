from discord.ext import commands
import discord
import json
import os

class SelfRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_path = os.path.join("data", "selfroles")
        self.file_path = os.path.join(self.data_path, "settings.json")
        self.load_settings()
    
    def load_settings(self):
        """Load settings from file"""
        if not os.path.exists(self.data_path):
            os.makedirs(self.data_path)
        
        if not os.path.exists(self.file_path):
            self.settings = {}
            self.save_settings()
        else:
            try:
                with open(self.file_path, 'r') as f:
                    self.settings = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                self.settings = {}
                self.save_settings()
    
    def save_settings(self):
        """Save settings to file"""
        with open(self.file_path, 'w') as f:
            json.dump(self.settings, f, indent=4)
    
    def get_server_settings(self, guild_id):
        """Get settings for a specific server"""
        if str(guild_id) not in self.settings:
            self.settings[str(guild_id)] = {
                "roles": [],
                "enabled": True
            }
            self.save_settings()
        return self.settings[str(guild_id)]
    
    def is_role_allowed(self, guild_id, role_id):
        """Check if a role is allowed for self-assignment"""
        server_settings = self.get_server_settings(guild_id)
        return str(role_id) in server_settings["roles"]
    
    @commands.group(name="selfrole", aliases=["sr"], invoke_without_command=True)
    @commands.guild_only()
    async def selfrole(self, ctx):
        """Self role management commands"""
        await ctx.send_help(ctx.command)
    
    @selfrole.command(name="add")
    @commands.has_permissions(manage_roles=True)
    async def add_role(self, ctx, role: discord.Role):
        """Add a role to the self-assignable list"""
        if role.position >= ctx.author.top_role.position:
            await ctx.send("You can't manage a role that is higher or equal to your top role!")
            return
            
        if role.position >= ctx.me.top_role.position:
            await ctx.send("I can't manage a role that is higher or equal to my top role!")
            return
        
        server_settings = self.get_server_settings(ctx.guild.id)
        if str(role.id) in server_settings["roles"]:
            await ctx.send(f"{role.mention} is already in the self-assignable roles list!")
            return
        
        server_settings["roles"].append(str(role.id))
        self.save_settings()
        await ctx.send(f"Added {role.mention} to the self-assignable roles list!")
    
    @selfrole.command(name="remove")
    @commands.has_permissions(manage_roles=True)
    async def remove_role(self, ctx, role: discord.Role):
        """Remove a role from the self-assignable list"""
        server_settings = self.get_server_settings(ctx.guild.id)
        if str(role.id) not in server_settings["roles"]:
            await ctx.send(f"{role.mention} is not in the self-assignable roles list!")
            return
        
        server_settings["roles"].remove(str(role.id))
        self.save_settings()
        await ctx.send(f"Removed {role.mention} from the self-assignable roles list!")
    
    @selfrole.command(name="list")
    async def list_roles(self, ctx):
        """List all self-assignable roles"""
        server_settings = self.get_server_settings(ctx.guild.id)
        roles = server_settings["roles"]
        
        if not roles:
            await ctx.send("There are no self-assignable roles set up for this server.")
            return
        
        role_objects = []
        for role_id in roles:
            role = ctx.guild.get_role(int(role_id))
            if role:
                role_objects.append(role.mention)
        
        if not role_objects:
            await ctx.send("No roles found in the self-assignable list. Please check the settings.")
            return
        
        role_list = "\n".join(role_objects)
        await ctx.send(f"Self-assignable roles:\n{role_list}")
    
    @selfrole.command(name="toggle")
    @commands.has_permissions(manage_roles=True)
    async def toggle_selfroles(self, ctx):
        """Toggle self-roles on/off for the server"""
        server_settings = self.get_server_settings(ctx.guild.id)
        server_settings["enabled"] = not server_settings["enabled"]
        self.save_settings()
        
        status = "enabled" if server_settings["enabled"] else "disabled"
        await ctx.send(f"Self-roles are now {status} for this server.")
    
    @selfrole.command(name="enable")
    @commands.has_permissions(manage_roles=True)
    async def enable_selfroles(self, ctx):
        """Enable self-roles for the server"""
        server_settings = self.get_server_settings(ctx.guild.id)
        server_settings["enabled"] = True
        self.save_settings()
        await ctx.send("Self-roles are now enabled for this server.")
    
    @selfrole.command(name="disable")
    @commands.has_permissions(manage_roles=True)
    async def disable_selfroles(self, ctx):
        """Disable self-roles for the server"""
        server_settings = self.get_server_settings(ctx.guild.id)
        server_settings["enabled"] = False
        self.save_settings()
        await ctx.send("Self-roles are now disabled for this server.")
    
    @commands.command(name="joinrole")
    @commands.guild_only()
    async def join_role(self, ctx, role: discord.Role):
        """Join a self-assignable role"""
        if not self.get_server_settings(ctx.guild.id)["enabled"]:
            await ctx.send("Self-roles are currently disabled on this server.")
            return
        
        if not self.is_role_allowed(ctx.guild.id, role.id):
            await ctx.send(f"{role.mention} is not a self-assignable role!")
            return
        
        if role in ctx.author.roles:
            await ctx.send(f"You already have the {role.mention} role!")
            return
        
        try:
            await ctx.author.add_roles(role, reason="Self-assignable role")
            await ctx.send(f"Successfully gave you the {role.mention} role!")
        except discord.Forbidden:
            await ctx.send("I don't have permission to add that role to you!")
        except discord.HTTPException:
            await ctx.send("An error occurred while trying to add the role!")
    
    @commands.command(name="leaverole")
    @commands.guild_only()
    async def leave_role(self, ctx, role: discord.Role):
        """Leave a self-assignable role"""
        if not self.get_server_settings(ctx.guild.id)["enabled"]:
            await ctx.send("Self-roles are currently disabled on this server.")
            return
        
        if not self.is_role_allowed(ctx.guild.id, role.id):
            await ctx.send(f"{role.mention} is not a self-assignable role!")
            return
        
        if role not in ctx.author.roles:
            await ctx.send(f"You don't have the {role.mention} role!")
            return
        
        try:
            await ctx.author.remove_roles(role, reason="Self-assignable role")
            await ctx.send(f"Successfully removed the {role.mention} role from you!")
        except discord.Forbidden:
            await ctx.send("I don't have permission to remove that role from you!")
        except discord.HTTPException:
            await ctx.send("An error occurred while trying to remove the role!")

    @commands.Cog.listener()
    async def on_ready(self):
        """Cog loaded event"""
        print(f"SelfRoles cog loaded!")

def setup(bot):
    bot.add_cog(SelfRoles(bot))
