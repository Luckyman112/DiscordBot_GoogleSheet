import discord
from discord.ext import commands

import database
from config import TARGET_ROLE_ID, log_ds


class Registration(commands.Cog):
    """Добавление/удаление пользователей в таблице по роли."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await log_ds(f"🚪 Пользователь {member.name} покинул сервер. Очистка таблицы...")
        await database.remove_user_from_sheet(member.id)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        role = discord.utils.get(after.roles, id=TARGET_ROLE_ID)
        was_in_before = discord.utils.get(before.roles, id=TARGET_ROLE_ID)
        if role and not was_in_before:
            await log_ds(f"📥 Пользователь {after.name} получил роль! Добавляю...")
            await database.add_user_to_sheet(after)
        elif not role and was_in_before:
            await log_ds(f"📤 Пользователь {after.name} потерял роль! Удаляю из таблицы...")
            await database.remove_user_from_sheet(after.id)


async def setup(bot):
    await bot.add_cog(Registration(bot))
