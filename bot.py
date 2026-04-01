import discord
import gspread
import random
import asyncio
import json
import os
import datetime
from discord.ext import commands, tasks
from oauth2client.service_account import ServiceAccountCredentials

# ==========================================
# 1. НАСТРОЙКИ (ВСТАВЬ СВОИ ДАННЫЕ)
# ==========================================
SHEET_ID = os.getenv("SHEET_ID")
TOKEN = os.getenv("DISCORD_TOKEN") 

TARGET_ROLE_ID = 1487969672178438295  # ID роли для автодобавления
MEMORY_FILE = 'waiting_answers.json'  # Файл для сохранения памяти бота

# --- НАСТРОЙКИ ДЛЯ ЖАЛОБ И ЛОГОВ ---
MODERATOR_ROLE_ID = 1488001276804337685 # Роль Модератора вопросов
ADMIN_CHANNEL_ID = 1487859608658772010  # Канал для уведомлений и ЛОГОВ

# --- НАСТРОЙКА ВРЕМЕНИ РАССЫЛКИ ---
# ВАЖНО: Время здесь указывается по UTC (по Гринвичу). 
# Если твой сервер (или нужный часовой пояс) находится в МСК (UTC+3), 
# то чтобы рассылка была в 08:00 утра по МСК, здесь нужно указать hour=5.
SEND_TIME = datetime.time(hour=0, minute=55) 

# ==========================================
# 2. ИНИЦИАЛИЗАЦИЯ GOOGLE TABLES
# ==========================================
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
sh = client.open_by_key(SHEET_ID)
worksheet = sh.get_worksheet(0)

# ==========================================
# 3. НАСТРОЙКА ДИСКОРД БОТА И ПАМЯТИ
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
intents.members = True 
bot = commands.Bot(command_prefix="!", intents=intents)

# Словарь для хранения ожидаемых ответов: {user_id: [row, col, days_ignored]}
waiting_answers = {}
# Единый стиль линий для всей таблицы
BORDER_STYLE = {"style": "SOLID_MEDIUM", "color": {"red": 0, "green": 0, "blue": 0}}

# --- УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ЛОГИРОВАНИЯ ---
async def log_ds(text):
    print(text) # Вывод в консоль сервера
    try:
        channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if channel:
            await channel.send(f"`{text}`") # Отправка в канал Дискорда
    except Exception:
        pass

# --- ФУНКЦИИ ПАМЯТИ ---
async def load_memory():
    global waiting_answers
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                waiting_answers = {}
                for k, v in data.items():
                    if len(v) == 2:
                        v.append(0)
                    waiting_answers[int(k)] = v
            await log_ds(f"🧠 Память загружена. Должников в базе: {len(waiting_answers)}")
        except Exception as e:
            await log_ds(f"❌ Ошибка загрузки памяти: {e}")
            waiting_answers = {}

async def save_memory():
    try:
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(waiting_answers, f)
    except Exception as e:
        await log_ds(f"❌ Ошибка сохранения памяти: {e}")

# ==========================================
# 4. ФУНКЦИИ РАБОТЫ С ТАБЛИЦЕЙ
# ==========================================

# Удаление пользователя из таблицы
async def remove_user_from_sheet(user_id):
    try:
        row_3 = worksheet.row_values(3)
        target_id = f"ID-{user_id}"
        
        if target_id in row_3:
            col_idx = row_3.index(target_id)
            
            body = {
                "requests": [{
                    "deleteDimension": {
                        "range": {
                            "sheetId": worksheet.id,
                            "dimension": "COLUMNS",
                            "startIndex": col_idx,      
                            "endIndex": col_idx + 2     
                        }
                    }
                }]
            }
            sh.batch_update(body)
            
            if int(user_id) in waiting_answers:
                del waiting_answers[int(user_id)]
                await save_memory()
                
            await log_ds(f"✅ Колонки пользователя {user_id} удалены! Таблица сдвинута.")
    except Exception as e:
        await log_ds(f"❌ Ошибка при удалении данных {user_id}: {e}")

# Добавление пользователя в таблицу
async def add_user_to_sheet(member):
    try:
        row_3 = worksheet.row_values(3)
        user_id_str = str(member.id)
        
        existing_ids = [cell.replace("ID-", "").strip() for cell in row_3 if cell.startswith("ID-")]
        if user_id_str in existing_ids:
            return False

        current_col_idx = len(row_3) + 1
        questions_count = len(worksheet.col_values(3)[3:])

        if member.display_name != member.name:
            user_header = f"{member.display_name} ({member.name}) answ"
        else:
            user_header = f"{member.name} answ"

        worksheet.update_cell(2, current_col_idx, "Пауза ➔")
        worksheet.update(gspread.utils.rowcol_to_a1(2, current_col_idx + 1), [[False]], value_input_option='USER_ENTERED')
        
        worksheet.update_cell(3, current_col_idx, f"ID-{user_id_str}")
        worksheet.update_cell(3, current_col_idx + 1, user_header)

        if questions_count > 0:
            start_row = 4
            end_row = 4 + questions_count - 1
            
            cell_range = f"{gspread.utils.rowcol_to_a1(start_row, current_col_idx)}:{gspread.utils.rowcol_to_a1(end_row, current_col_idx)}"
            worksheet.update(cell_range, [[False] for _ in range(questions_count)], value_input_option='USER_ENTERED')
            
            body = {
                "requests": [
                    {
                        "setDataValidation": {
                            "range": {
                                "sheetId": worksheet.id, "startRowIndex": 1, "endRowIndex": 2,
                                "startColumnIndex": current_col_idx, "endColumnIndex": current_col_idx + 1
                            },
                            "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True}
                        }
                    },
                    {
                        "setDataValidation": {
                            "range": {
                                "sheetId": worksheet.id, "startRowIndex": start_row - 1, "endRowIndex": end_row,
                                "startColumnIndex": current_col_idx - 1, "endColumnIndex": current_col_idx
                            },
                            "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True}
                        }
                    },
                    {
                        "updateBorders": {
                            "range": {
                                "sheetId": worksheet.id, "startRowIndex": 1, "endRowIndex": end_row,
                                "startColumnIndex": current_col_idx - 1, "endColumnIndex": current_col_idx + 1
                            },
                            "top": BORDER_STYLE, "bottom": BORDER_STYLE, "left": BORDER_STYLE,
                            "right": BORDER_STYLE, "innerHorizontal": BORDER_STYLE, "innerVertical": BORDER_STYLE
                        }
                    }
                ]
            }
            sh.batch_update(body)
        
        await log_ds(f"✅ Добавлен {user_header} с чекбоксами и рамками.")
        return True
    except Exception as e:
        await log_ds(f"❌ Ошибка при добавлении {member.name}: {e}")
        return False

# Синхронизация ролей и никнеймов
async def sync_sheet_with_roles():
    await log_ds("⚙️ Синхронизация таблицы: проверка ролей и обновлений никнеймов...")
    try:
        # Собираем всех пользователей с нужной ролью в словарь {id: объект_пользователя}
        valid_members = {}
        for guild in bot.guilds:
            for member in guild.members:
                if not member.bot and discord.utils.get(member.roles, id=TARGET_ROLE_ID):
                    valid_members[str(member.id)] = member

        valid_ids = set(valid_members.keys())

        # --- 1. УДАЛЕНИЕ ТЕХ, КТО ПОТЕРЯЛ РОЛЬ ---
        row_3 = worksheet.row_values(3)
        to_delete = []
        for col_idx, cell_value in enumerate(row_3, start=1):
            clean_cell = cell_value.strip()
            if clean_cell.startswith("ID-"):
                uid = clean_cell.replace("ID-", "")
                if uid not in valid_ids:
                    to_delete.append((uid, col_idx))

        if to_delete:
            to_delete.sort(key=lambda x: x[1], reverse=True) 
            requests = []
            memory_changed = False
            for uid, col_idx in to_delete:
                requests.append({
                    "deleteDimension": {
                        "range": {"sheetId": worksheet.id, "dimension": "COLUMNS", "startIndex": col_idx - 1, "endIndex": col_idx + 1}
                    }
                })
                if int(uid) in waiting_answers:
                    del waiting_answers[int(uid)]
                    memory_changed = True

            sh.batch_update({"requests": requests})
            if memory_changed: await save_memory()
            await log_ds(f"🧹 Удалено людей без роли: {len(requests)}")

        # --- 2. ПРОВЕРКА НИКНЕЙМОВ И ДОБАВЛЕНИЕ НОВИЧКОВ ---
        row_3_updated = worksheet.row_values(3)
        existing_ids = set()
        cells_to_update = []
        
        for col_idx, cell_value in enumerate(row_3_updated, start=1):
            clean_cell = cell_value.strip()
            if clean_cell.startswith("ID-"):
                uid_str = clean_cell.replace("ID-", "")
                existing_ids.add(uid_str)
                
                # Проверяем никнейм текущего пользователя
                if uid_str in valid_members:
                    member = valid_members[uid_str]
                    
                    if member.display_name != member.name:
                        expected_header = f"{member.display_name} ({member.name}) answ"
                    else:
                        expected_header = f"{member.name} answ"
                        
                    current_header = ""
                    if col_idx < len(row_3_updated): 
                        current_header = row_3_updated[col_idx]
                        
                    if current_header != expected_header:
                        cells_to_update.append(gspread.Cell(row=3, col=col_idx + 1, value=expected_header))

        if cells_to_update:
            worksheet.update_cells(cells_to_update, value_input_option='USER_ENTERED')
            await log_ds(f"🔄 Обновлено никнеймов в таблице: {len(cells_to_update)}")

        # Добавляем новых пользователей
        for uid_str, member in valid_members.items():
            if uid_str not in existing_ids:
                await add_user_to_sheet(member)
                await asyncio.sleep(1)

        await log_ds("✅ Синхронизация завершена!")
    except Exception as e:
        await log_ds(f"❌ Ошибка при синхронизации: {e}")


# Синхронизация новых вопросов
async def sync_new_questions():
    await log_ds("🔍 Проверка новых вопросов, автонумерация и дорисовка рамок...")
    try:
        all_data = worksheet.get_all_values()
        if len(all_data) < 3: return
        
        questions = [row[2] for row in all_data[3:] if len(row) > 2 and row[2].strip()]
        num_questions = len(questions)
        if num_questions == 0: return

        cells_to_update = []
        
        for r_idx in range(3, 3 + num_questions):
            expected_num = str(r_idx - 2)
            has_num = False
            if r_idx < len(all_data) and len(all_data[r_idx]) > 1:
                if str(all_data[r_idx][1]).strip() == expected_num:
                    has_num = True
            
            if not has_num:
                cells_to_update.append(gspread.Cell(row=r_idx + 1, col=2, value=expected_num))

        row_3 = all_data[2]
        user_cols = [i + 1 for i, val in enumerate(row_3) if val.strip().startswith("ID-")]
        
        start_row = 4
        end_row = 3 + num_questions
        requests = []

        requests.append({
            "updateBorders": {
                "range": {
                    "sheetId": worksheet.id, 
                    "startRowIndex": start_row - 1, 
                    "endRowIndex": end_row,         
                    "startColumnIndex": 1,          
                    "endColumnIndex": 3             
                },
                "top": BORDER_STYLE, "bottom": BORDER_STYLE, "left": BORDER_STYLE,
                "right": BORDER_STYLE, "innerHorizontal": BORDER_STYLE, "innerVertical": BORDER_STYLE
            }
        })

        if user_cols:
            for col in user_cols:
                for r_idx in range(start_row - 1, end_row):
                    if r_idx >= len(all_data) or col - 1 >= len(all_data[r_idx]) or not str(all_data[r_idx][col - 1]).strip():
                        cells_to_update.append(gspread.Cell(row=r_idx + 1, col=col, value='FALSE'))

                requests.append({
                    "setDataValidation": {
                        "range": {"sheetId": worksheet.id, "startRowIndex": start_row - 1, "endRowIndex": end_row, "startColumnIndex": col - 1, "endColumnIndex": col},
                        "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True}
                    }
                })
                requests.append({
                    "updateBorders": {
                        "range": {"sheetId": worksheet.id, "startRowIndex": 1, "endRowIndex": end_row, "startColumnIndex": col - 1, "endColumnIndex": col + 1},
                        "top": BORDER_STYLE, "bottom": BORDER_STYLE, "left": BORDER_STYLE, "right": BORDER_STYLE, "innerHorizontal": BORDER_STYLE, "innerVertical": BORDER_STYLE
                    }
                })

        if cells_to_update:
            worksheet.update_cells(cells_to_update, value_input_option='USER_ENTERED')
        if requests:
            sh.batch_update({"requests": requests})
            
        await log_ds("✅ Таблица успешно синхронизирована (добавлены номера, рамки для вопросов и чекбоксы)!")
        return True
    except Exception as e:
        await log_ds(f"❌ Ошибка при синхронизации новых вопросов: {e}")
        return False

# ==========================================
# 5. ЦИКЛИЧЕСКАЯ РАССЫЛКА ВОПРОСОВ (ПО ВРЕМЕНИ)
# ==========================================
@tasks.loop(time=SEND_TIME)
async def individual_random_mailing():
    global waiting_answers
    await log_ds("\n--- 🚀 НАЧАЛО ЦИКЛА РАССЫЛКИ ---")
    try:
        await sync_new_questions()

        row_2 = worksheet.row_values(2) 
        row_3 = worksheet.row_values(3)
        all_questions = worksheet.col_values(3)[3:]
        
        users_processed = 0
        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)

        for col_idx, cell_value in enumerate(row_3, start=1):
            if cell_value.strip().startswith("ID-"):
                users_processed += 1
                user_id_raw = cell_value.replace("ID-", "").strip()
                if not user_id_raw.isdigit(): continue
                user_id = int(user_id_raw)

                # Проверка на паузу
                is_paused = 'FALSE'
                if len(row_2) > col_idx:
                    is_paused = str(row_2[col_idx]).strip().upper()
                if is_paused == 'TRUE':
                    await log_ds(f"⏸️ Пользователь {user_id} на паузе. Пропускаем.")
                    continue

                # --- ЛОГИКА ДОЛЖНИКОВ (ЖДЕМ ОТВЕТ) ---
                if user_id in waiting_answers:
                    waiting_answers[user_id][2] += 1
                    await save_memory()
                    
                    days_ignored = waiting_answers[user_id][2]
                    
                    if days_ignored >= 1:
                        if admin_channel:
                            await admin_channel.send(f"<@&{MODERATOR_ROLE_ID}> ⚠️ Внимание! Пользователь <@{user_id}> не отвечает на вопрос уже **{days_ignored} дня**!")
                        await log_ds(f"🚨 Жалоба на пользователя <@{user_id}> отправлена (игнор {days_ignored} дн.)")

                    try:
                        user = await bot.fetch_user(user_id)
                        embed = discord.Embed(
                            title="⏳ Напоминание",
                            description="Я всё ещё жду твой ответ на предыдущий вопрос!\nНапиши его сюда, чтобы я всё записал.",
                            color=discord.Color.orange()
                        )
                        await user.send(embed=embed)
                        await log_ds(f"⏳ Отправлено напоминание пользователю <@{user_id}>")
                        await asyncio.sleep(1.5)
                    except: pass
                    continue 

                # --- ВЫДАЧА НОВОГО ВОПРОСА ---
                user_column_data = worksheet.col_values(col_idx)[3:]
                available_indices = []
                for i in range(len(all_questions)):
                    if not all_questions[i].strip(): continue
                    status = user_column_data[i].upper() if i < len(user_column_data) else 'FALSE'
                    if status == 'FALSE':
                        available_indices.append(i)

                try:
                    user = await bot.fetch_user(user_id)
                    if available_indices:
                        chosen_idx = random.choice(available_indices)
                        chosen_q = all_questions[chosen_idx]
                        q_number = chosen_idx + 1 
                        
                        waiting_answers[user_id] = [chosen_idx + 4, col_idx + 1, 0]
                        await save_memory()
                        
                        embed = discord.Embed(
                            title=f"🎲 Твой вопрос №{q_number} на сегодня",
                            description=f"**{chosen_q}**",
                            color=discord.Color.blue()
                        )
                        embed.set_footer(text="Ответь на это сообщение, чтобы записать ответ в таблицу.")
                        await user.send(embed=embed)
                        
                        worksheet.update_cell(1, col_idx + 1, "")
                        await log_ds(f"✅ Отправлен новый вопрос пользователю {user_id}")
                    else:
                        await log_ds(f"✨ У пользователя {user_id} нет доступных вопросов (на все ответил).")
                        
                    await asyncio.sleep(1.5)
                    
                except discord.Forbidden:
                    await log_ds(f"⚠️ Пользователь {user_id} закрыл ЛС! Жалуюсь модераторам.")
                    worksheet.update_cell(1, col_idx + 1, "⚠️ ЛС закрыты, свяжитесь!")
                    
                    if admin_channel:
                        await admin_channel.send(f"<@&{MODERATOR_ROLE_ID}> 🚨 У пользователя <@{user_id}> **закрыты ЛС**! Я не могу отправить ему вопрос.")
                    
                    if user_id in waiting_answers: 
                        del waiting_answers[user_id]
                        await save_memory()
                except Exception as e:
                    await log_ds(f"❌ Не удалось отправить {user_id}: {e}")
                    
        await log_ds(f"--- 🏁 Рассылка завершена. Проверено пользователей: {users_processed} ---")
        await log_ds("💤 Ухожу в ожидание до следующего запуска по расписанию...\n")
        
    except Exception as e:
        await log_ds(f"❌ Ошибка в рассылке: {e}")

# ==========================================
# 6. СОБЫТИЯ DISCORD
# ==========================================
@bot.event
async def on_ready():
    await log_ds(f'--- Бот {bot.user} запущен ---')
    await load_memory() 
    await sync_sheet_with_roles()
    if not individual_random_mailing.is_running():
        individual_random_mailing.start()

@bot.event
async def on_member_remove(member):
    await log_ds(f"🚪 Пользователь {member.name} покинул сервер. Очистка таблицы...")
    await remove_user_from_sheet(member.id)

@bot.event
async def on_member_update(before, after):
    role = discord.utils.get(after.roles, id=TARGET_ROLE_ID)
    was_in_before = discord.utils.get(before.roles, id=TARGET_ROLE_ID)
    if role and not was_in_before:
        await log_ds(f"📥 Пользователь {after.name} получил роль! Добавляю...")
        await add_user_to_sheet(after)
    elif not role and was_in_before:
        await log_ds(f"📤 Пользователь {after.name} потерял роль! Удаляю из таблицы...")
        await remove_user_from_sheet(after.id)

@bot.event
async def on_message(message):
    if message.author == bot.user: return
    
    if isinstance(message.channel, discord.DMChannel) and message.author.id in waiting_answers:
        row, col, _ = waiting_answers[message.author.id]
        try:
            worksheet.update_cell(row, col, message.content) 
            worksheet.update_cell(row, col - 1, True)        
            
            del waiting_answers[message.author.id]
            await save_memory()
            
            embed = discord.Embed(
                title="✅ Успешно!",
                description="Твой ответ записан в таблицу, спасибо!",
                color=discord.Color.green()
            )
            await message.author.send(embed=embed)
            await log_ds(f"📝 Ответ от {message.author.name} сохранен.")
        except Exception as e:
            await log_ds(f"❌ Ошибка записи ответа: {e}")
            
    await bot.process_commands(message)

# ==========================================
# 7. КОМАНДЫ
# ==========================================
@bot.command()
async def го(ctx):
    await ctx.send("Запускаю рассылку вручную... 🎲")
    await individual_random_mailing()

@bot.command()
async def обновить_таблицу(ctx):
    await ctx.send("Проверяю обновления (роли, никнеймы, рамки)... ⏳")
    await sync_sheet_with_roles() # Теперь принудительно обновит никнеймы и роли
    result = await sync_new_questions() # Затем нарисует вопросы
    if result: 
        await ctx.send("✅ Готово! Таблица полностью синхронизирована.")
    else: 
        await ctx.send("❌ Произошла ошибка или новые вопросы не найдены.")

@bot.command(name="вопрос")
@commands.has_any_role(MODERATOR_ROLE_ID)
async def send_manual_question(ctx, member: discord.Member):
    """Отправить случайный вопрос конкретному пользователю: !вопрос @User"""
    await ctx.send(f"⏳ Ищу свободный вопрос для {member.display_name}...")
    
    try:
        row_3 = worksheet.row_values(3)
        user_id_str = f"ID-{member.id}"
        
        if user_id_str not in row_3:
            return await ctx.send("❌ Этого пользователя нет в таблице (возможно, у него нет нужной роли).")
        
        col_idx = row_3.index(user_id_str) + 1 
        
        if member.id in waiting_answers:
            return await ctx.send(f"⚠️ У {member.display_name} уже висит неотвеченный вопрос. Сначала пусть ответит на него!")

        all_questions = worksheet.col_values(3)[3:]
        user_column_data = worksheet.col_values(col_idx)[3:]
        
        available_indices = []
        for i in range(len(all_questions)):
            if not all_questions[i].strip(): continue
            status = user_column_data[i].upper() if i < len(user_column_data) else 'FALSE'
            if status == 'FALSE':
                available_indices.append(i)

        if not available_indices:
            return await ctx.send(f"✨ {member.display_name} уже ответил на все вопросы в таблице!")

        chosen_idx = random.choice(available_indices)
        chosen_q = all_questions[chosen_idx]
        q_number = chosen_idx + 1
        
        waiting_answers[member.id] = [chosen_idx + 4, col_idx + 1, 0]
        await save_memory()
        
        embed = discord.Embed(
            title=f"🎯 Персональный вопрос №{q_number}",
            description=f"**{chosen_q}**",
            color=discord.Color.purple()
        )
        embed.set_footer(text="Модератор вызвал этот вопрос вручную.")
        
        await member.send(embed=embed)
        await ctx.send(f"✅ Вопрос №{q_number} успешно отправлен в ЛС пользователю {member.display_name}.")
        await log_ds(f"👨‍⚖️ Модератор {ctx.author.name} вручную пнул {member.name}")

    except discord.Forbidden:
        await ctx.send(f"🚫 Не удалось отправить сообщение: у {member.display_name} закрыты ЛС.")
    except Exception as e:
        await ctx.send(f"❌ Произошла ошибка: {e}")

bot.run(TOKEN)