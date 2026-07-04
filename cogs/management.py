from discord.ext import commands

import database
from config import AUTO_START_MAILING, MODERATOR_ROLE_ID, log_ds


class Management(commands.Cog):
    """Запуск бота, ручные команды синхронизации и обработка ошибок команд."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await log_ds(f'--- Бот {self.bot.user} запущен ---')
        await database.load_memory()
        await database.sync_sheet_with_roles()

        reports_cog = self.bot.get_cog("Reports")
        if AUTO_START_MAILING and reports_cog and not reports_cog.individual_random_mailing.is_running():
            reports_cog.individual_random_mailing.start()
        elif not AUTO_START_MAILING:
            await log_ds("⏸ Автозапуск рассылки отключён (AUTO_START_MAILING=false). Запустить вручную: !go")

    @commands.command(name="go")
    @commands.has_any_role(MODERATOR_ROLE_ID)
    async def go(self, ctx):
        """Запустить рассылку вопросов вручную: !go"""
        await ctx.send("Запускаю рассылку вручную... 🎲")
        from cogs.reports import run_mailing_cycle
        await run_mailing_cycle()

    @commands.command(name="sync")
    @commands.has_any_role(MODERATOR_ROLE_ID)
    async def sync(self, ctx):
        """Синхронизировать таблицу (роли, никнеймы, новые вопросы): !sync"""
        await ctx.send("Проверяю обновления (роли, никнеймы, рамки)... ⏳")
        await database.sync_sheet_with_roles()  # Теперь принудительно обновит никнеймы и роли
        result = await database.sync_new_questions()  # Затем нарисует вопросы
        if result:
            await ctx.send("✅ Готово! Таблица полностью синхронизирована.")
        else:
            await ctx.send("❌ Произошла ошибка или новые вопросы не найдены.")

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.MissingAnyRole):
            await ctx.send("🚫 У тебя нет прав для этой команды.")
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send("❌ Не нашёл такого участника на сервере.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"⚠️ Не хватает аргумента: `{error.param.name}`.")
        elif isinstance(error, commands.CommandNotFound):
            return
        else:
            await log_ds(f"❌ Необработанная ошибка команды `{ctx.command}`: {error}")
            await ctx.send("❌ Произошла непредвиденная ошибка при выполнении команды.")


async def setup(bot):
    await bot.add_cog(Management(bot))
