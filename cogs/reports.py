import asyncio
import datetime
import random

import discord
from discord.ext import commands, tasks

import database
from config import (
    ADMIN_CHANNEL_ID, ANSWER_EDIT_WINDOW_SECONDS, ESCALATION_THRESHOLD_DAYS,
    MODERATOR_ROLE_ID, SEND_TIME, bot, log_ds, plural_days,
)

# {user_id: (row, col, answered_at)} — короткое окно для правки последнего ответа.
# Не сохраняется на диск: если бот перезапустится в эти 10 минут, окно просто сгорит.
last_answers = {}


def _progress_footer(answered, total):
    percent = round(answered / total * 100) if total else 0
    return f"Прогресс: {answered}/{total} отвечено ({percent}%) · Ответь на это сообщение, чтобы записать ответ."


class ReminderView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⏸ Отложить на день", style=discord.ButtonStyle.secondary, custom_id="quiz_snooze")
    async def snooze(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = database.waiting_answers.get(interaction.user.id)
        if not entry:
            return await interaction.response.send_message("У тебя сейчас нет активного вопроса на ожидании.", ephemeral=True)
        entry[2] = max(0, entry[2] - 1)
        await database.save_memory()
        await interaction.response.send_message("Хорошо, напомню завтра снова.", ephemeral=True)


class QuestionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="❓ Вопрос непонятен", style=discord.ButtonStyle.secondary, custom_id="quiz_unclear")
    async def unclear(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = database.waiting_answers.get(interaction.user.id)
        if not entry:
            return await interaction.response.send_message("У тебя сейчас нет активного вопроса.", ephemeral=True)
        row, _, _ = entry
        question_text = await database.get_question_text(row)
        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            await admin_channel.send(
                f"<@&{MODERATOR_ROLE_ID}> ❓ Пользователь <@{interaction.user.id}> не понял вопрос "
                f"(строка {row}): «{question_text}». Возможно, стоит уточнить формулировку."
            )
        await interaction.response.send_message("Окей, передал модераторам — уточнят формулировку.", ephemeral=True)


async def run_mailing_cycle():
    """Один цикл рассылки вопросов/напоминаний. Вызывается по расписанию и вручную (!go)."""
    await log_ds("\n--- 🚀 НАЧАЛО ЦИКЛА РАССЫЛКИ ---")
    try:
        await database.sync_new_questions()

        row_2 = await database.run_blocking(database.worksheet.row_values, 2)
        row_3 = await database.run_blocking(database.worksheet.row_values, 3)
        col_3 = await database.run_blocking(database.worksheet.col_values, 3)
        all_questions = col_3[3:]
        total_questions = len([q for q in all_questions if q.strip()])

        users_processed = 0
        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)

        for col_idx, cell_value in enumerate(row_3, start=1):
            if cell_value.strip().startswith("ID-"):
                users_processed += 1
                user_id_raw = cell_value.replace("ID-", "").strip()
                if not user_id_raw.isdigit():
                    continue
                user_id = int(user_id_raw)

                # Проверка на паузу
                is_paused = 'FALSE'
                if len(row_2) > col_idx:
                    is_paused = str(row_2[col_idx]).strip().upper()
                if is_paused == 'TRUE':
                    await log_ds(f"⏸️ Пользователь {user_id} на паузе. Пропускаем.")
                    continue

                # --- ЛОГИКА ДОЛЖНИКОВ (ЖДЕМ ОТВЕТ) ---
                if user_id in database.waiting_answers:
                    database.waiting_answers[user_id][2] += 1
                    await database.save_memory()

                    days_ignored = database.waiting_answers[user_id][2]

                    if days_ignored >= ESCALATION_THRESHOLD_DAYS:
                        if admin_channel:
                            await admin_channel.send(f"<@&{MODERATOR_ROLE_ID}> ⚠️ Внимание! Пользователь <@{user_id}> не отвечает на вопрос уже **{plural_days(days_ignored)}**!")
                        await log_ds(f"🚨 Жалоба на пользователя <@{user_id}> отправлена (игнор {plural_days(days_ignored)})")

                    try:
                        user = await bot.fetch_user(user_id)
                        embed = discord.Embed(
                            title="⏳ Напоминание",
                            description="Я всё ещё жду твой ответ на предыдущий вопрос!\nНапиши его сюда, чтобы я всё записал.",
                            color=discord.Color.orange()
                        )
                        await user.send(embed=embed, view=ReminderView())
                        await log_ds(f"⏳ Отправлено напоминание пользователю <@{user_id}>")
                        await asyncio.sleep(1.5)
                    except discord.Forbidden:
                        await log_ds(f"⚠️ Пользователь {user_id} закрыл ЛС, напоминание не доставлено!")
                        if admin_channel:
                            await admin_channel.send(f"<@&{MODERATOR_ROLE_ID}> 🚨 У пользователя <@{user_id}> **закрыты ЛС** — не могу напомнить про должок!")
                    except Exception as e:
                        await log_ds(f"❌ Не удалось отправить напоминание {user_id}: {e}")
                    continue

                # --- ВЫДАЧА НОВОГО ВОПРОСА ---
                user_column_data_full = await database.run_blocking(database.worksheet.col_values, col_idx)
                user_column_data = user_column_data_full[3:]
                available_indices = []
                for i in range(len(all_questions)):
                    if not all_questions[i].strip():
                        continue
                    status = user_column_data[i].upper() if i < len(user_column_data) else 'FALSE'
                    if status == 'FALSE':
                        available_indices.append(i)

                try:
                    user = await bot.fetch_user(user_id)
                    if available_indices:
                        chosen_idx = random.choice(available_indices)
                        chosen_q = all_questions[chosen_idx]
                        q_number = chosen_idx + 1
                        answered_so_far = total_questions - len(available_indices)

                        database.waiting_answers[user_id] = [chosen_idx + 4, col_idx + 1, 0]
                        await database.save_memory()

                        embed = discord.Embed(
                            title=f"🎲 Твой вопрос №{q_number} на сегодня",
                            description=f"**{chosen_q}**",
                            color=discord.Color.blue()
                        )
                        embed.set_footer(text=_progress_footer(answered_so_far, total_questions))
                        await user.send(embed=embed, view=QuestionView())

                        await database.run_blocking(database.worksheet.update_cell, 1, col_idx + 1, "")
                        await log_ds(f"✅ Отправлен новый вопрос пользователю {user_id}")
                    else:
                        await log_ds(f"✨ У пользователя {user_id} нет доступных вопросов (на все ответил).")

                    await asyncio.sleep(1.5)

                except discord.Forbidden:
                    await log_ds(f"⚠️ Пользователь {user_id} закрыл ЛС! Жалуюсь модераторам.")
                    await database.run_blocking(database.worksheet.update_cell, 1, col_idx + 1, "⚠️ ЛС закрыты, свяжитесь!")

                    if admin_channel:
                        await admin_channel.send(f"<@&{MODERATOR_ROLE_ID}> 🚨 У пользователя <@{user_id}> **закрыты ЛС**! Я не могу отправить ему вопрос.")

                    if user_id in database.waiting_answers:
                        del database.waiting_answers[user_id]
                        await database.save_memory()
                except Exception as e:
                    await log_ds(f"❌ Не удалось отправить {user_id}: {e}")

        await log_ds(f"--- 🏁 Рассылка завершена. Проверено пользователей: {users_processed} ---")
        await log_ds("💤 Ухожу в ожидание до следующего запуска по расписанию...\n")

    except Exception as e:
        await log_ds(f"❌ Ошибка в рассылке: {e}")


class Reports(commands.Cog):
    """Циклическая рассылка вопросов, приём ответов в ЛС, ручная выдача вопроса."""

    def __init__(self, bot):
        self.bot = bot
        bot.add_view(ReminderView())
        bot.add_view(QuestionView())

    def cog_unload(self):
        self.individual_random_mailing.cancel()

    @tasks.loop(time=SEND_TIME)
    async def individual_random_mailing(self):
        await run_mailing_cycle()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return

        if isinstance(message.channel, discord.DMChannel) and message.author.id in database.waiting_answers:
            row, col, _ = database.waiting_answers[message.author.id]
            try:
                await database.run_blocking(database.worksheet.update_cell, row, col, message.content)
                await database.run_blocking(database.worksheet.update_cell, row, col - 1, True)

                del database.waiting_answers[message.author.id]
                await database.save_memory()
                last_answers[message.author.id] = (row, col, datetime.datetime.utcnow())

                embed = discord.Embed(
                    title="✅ Успешно!",
                    description=(
                        "Твой ответ записан в таблицу, спасибо!\n"
                        "Если ошибся — есть 10 минут на правку командой `!editanswer <текст>`."
                    ),
                    color=discord.Color.green()
                )
                await message.author.send(embed=embed)
                await log_ds(f"📝 Ответ от {message.author.name} сохранен.")
            except Exception as e:
                await log_ds(f"❌ Ошибка записи ответа: {e}")
        # process_commands не нужен: Bot уже обрабатывает команды сам,
        # т.к. этот слушатель зарегистрирован через Cog.listener(), а не @bot.event.

    @commands.command(name="progress")
    async def check_progress(self, ctx):
        """Посмотреть свой прогресс прохождения устава: !progress"""
        col_idx = await database.find_user_column(ctx.author.id)
        if col_idx is None:
            return await ctx.send("❌ Тебя нет в таблице (возможно, нет нужной роли).")

        answered, total = await database.get_progress(col_idx)
        percent = round(answered / total * 100) if total else 0
        embed = discord.Embed(
            title="📊 Твой прогресс",
            description=f"Отвечено на **{answered} из {total}** вопросов ({percent}%).",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @commands.command(name="editanswer")
    async def edit_answer(self, ctx, *, new_text: str):
        """Исправить последний отправленный ответ (окно 10 минут): !editanswer <текст>"""
        entry = last_answers.get(ctx.author.id)
        if not entry:
            return await ctx.send("❌ Нет недавнего ответа для редактирования.")

        row, col, answered_at = entry
        age = (datetime.datetime.utcnow() - answered_at).total_seconds()
        if age > ANSWER_EDIT_WINDOW_SECONDS:
            del last_answers[ctx.author.id]
            return await ctx.send("⏰ Время на редактирование истекло (10 минут).")

        await database.run_blocking(database.worksheet.update_cell, row, col, new_text)
        await ctx.send("✅ Ответ обновлён.")
        await log_ds(f"✏️ Пользователь {ctx.author.name} отредактировал ответ (строка {row}).")

    @commands.command(name="ask")
    @commands.has_any_role(MODERATOR_ROLE_ID)
    async def send_manual_question(self, ctx, member: discord.Member):
        """Отправить случайный вопрос конкретному пользователю: !ask @User"""
        await ctx.send(f"⏳ Ищу свободный вопрос для {member.display_name}...")

        try:
            row_3 = await database.run_blocking(database.worksheet.row_values, 3)
            user_id_str = f"ID-{member.id}"

            if user_id_str not in row_3:
                return await ctx.send("❌ Этого пользователя нет в таблице (возможно, у него нет нужной роли).")

            col_idx = row_3.index(user_id_str) + 1

            if member.id in database.waiting_answers:
                return await ctx.send(f"⚠️ У {member.display_name} уже висит неотвеченный вопрос. Сначала пусть ответит на него!")

            col_3 = await database.run_blocking(database.worksheet.col_values, 3)
            all_questions = col_3[3:]
            total_questions = len([q for q in all_questions if q.strip()])
            user_col = await database.run_blocking(database.worksheet.col_values, col_idx)
            user_column_data = user_col[3:]

            available_indices = []
            for i in range(len(all_questions)):
                if not all_questions[i].strip():
                    continue
                status = user_column_data[i].upper() if i < len(user_column_data) else 'FALSE'
                if status == 'FALSE':
                    available_indices.append(i)

            if not available_indices:
                return await ctx.send(f"✨ {member.display_name} уже ответил на все вопросы в таблице!")

            chosen_idx = random.choice(available_indices)
            chosen_q = all_questions[chosen_idx]
            q_number = chosen_idx + 1
            answered_so_far = total_questions - len(available_indices)

            database.waiting_answers[member.id] = [chosen_idx + 4, col_idx + 1, 0]
            await database.save_memory()

            embed = discord.Embed(
                title=f"🎯 Персональный вопрос №{q_number}",
                description=f"**{chosen_q}**",
                color=discord.Color.purple()
            )
            embed.set_footer(text=f"Модератор вызвал этот вопрос вручную. {_progress_footer(answered_so_far, total_questions)}")

            await member.send(embed=embed, view=QuestionView())
            await ctx.send(f"✅ Вопрос №{q_number} успешно отправлен в ЛС пользователю {member.display_name}.")
            await log_ds(f"👨‍⚖️ Модератор {ctx.author.name} вручную пнул {member.name}")

        except discord.Forbidden:
            await ctx.send(f"🚫 Не удалось отправить сообщение: у {member.display_name} закрыты ЛС.")
        except Exception as e:
            await ctx.send(f"❌ Произошла ошибка: {e}")


async def setup(bot):
    await bot.add_cog(Reports(bot))
