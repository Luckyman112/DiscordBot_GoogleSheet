import asyncio
import json
import os

import discord
import gspread

from config import SHEET_ID, MEMORY_FILE, BORDER_STYLE, TARGET_ROLE_ID, bot, log_ds

# ==========================================
# ИНИЦИАЛИЗАЦИЯ GOOGLE TABLES
# ==========================================
client = gspread.service_account(filename="credentials.json")
sh = client.open_by_key(SHEET_ID)
worksheet = sh.get_worksheet(0)

# Словарь для хранения ожидаемых ответов: {user_id: [row, col, days_ignored]}
waiting_answers = {}


async def run_blocking(func, *args, **kwargs):
    """Выполняет блокирующий вызов gspread в отдельном потоке,
    чтобы не блокировать event loop бота."""
    return await asyncio.to_thread(func, *args, **kwargs)


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
# ФУНКЦИИ РАБОТЫ С ТАБЛИЦЕЙ
# ==========================================

# Удаление пользователя из таблицы
async def remove_user_from_sheet(user_id):
    try:
        row_3 = await run_blocking(worksheet.row_values, 3)
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
            await run_blocking(sh.batch_update, body)

            if int(user_id) in waiting_answers:
                del waiting_answers[int(user_id)]
                await save_memory()

            await log_ds(f"✅ Колонки пользователя {user_id} удалены! Таблица сдвинута.")
    except Exception as e:
        await log_ds(f"❌ Ошибка при удалении данных {user_id}: {e}")


# Добавление пользователя в таблицу
async def add_user_to_sheet(member):
    try:
        row_3 = await run_blocking(worksheet.row_values, 3)
        user_id_str = str(member.id)

        existing_ids = [cell.replace("ID-", "").strip() for cell in row_3 if cell.startswith("ID-")]
        if user_id_str in existing_ids:
            return False

        current_col_idx = len(row_3) + 1
        col_3_values = await run_blocking(worksheet.col_values, 3)
        questions_count = len(col_3_values[3:])

        if member.display_name != member.name:
            user_header = f"{member.display_name} ({member.name}) answ"
        else:
            user_header = f"{member.name} answ"

        await run_blocking(worksheet.update_cell, 2, current_col_idx, "Пауза ➔")
        await run_blocking(worksheet.update, gspread.utils.rowcol_to_a1(2, current_col_idx + 1), [[False]], value_input_option='USER_ENTERED')

        await run_blocking(worksheet.update_cell, 3, current_col_idx, f"ID-{user_id_str}")
        await run_blocking(worksheet.update_cell, 3, current_col_idx + 1, user_header)

        if questions_count > 0:
            start_row = 4
            end_row = 4 + questions_count - 1

            cell_range = f"{gspread.utils.rowcol_to_a1(start_row, current_col_idx)}:{gspread.utils.rowcol_to_a1(end_row, current_col_idx)}"
            await run_blocking(worksheet.update, cell_range, [[False] for _ in range(questions_count)], value_input_option='USER_ENTERED')

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
            await run_blocking(sh.batch_update, body)

        await log_ds(f"✅ Добавлен {user_header} с чекбоксами и рамками.")
        await send_welcome_message(member)
        return True
    except Exception as e:
        await log_ds(f"❌ Ошибка при добавлении {member.name}: {e}")
        return False


# Приветственное сообщение новичку — объясняет механику квиза
async def send_welcome_message(member):
    try:
        embed = discord.Embed(
            title="👋 Добро пожаловать!",
            description=(
                "Раз в день тебе будет приходить один вопрос по уставу — просто "
                "ответь на него прямо здесь, одним сообщением, и я запишу ответ в таблицу.\n\n"
                "Полезные команды (пиши мне в ЛС):\n"
                "`!progress` — сколько вопросов уже пройдено\n"
                "`!editanswer <текст>` — исправить последний ответ (в течение 10 минут после отправки)"
            ),
            color=discord.Color.blurple()
        )
        await member.send(embed=embed)
    except discord.Forbidden:
        await log_ds(f"⚠️ Не удалось отправить приветствие {member.name}: закрыты ЛС.")
    except Exception as e:
        await log_ds(f"❌ Ошибка при отправке приветствия {member.name}: {e}")


# Находит колонку пользователя в таблице по его Discord ID
async def find_user_column(user_id):
    row_3 = await run_blocking(worksheet.row_values, 3)
    target = f"ID-{user_id}"
    if target in row_3:
        return row_3.index(target) + 1
    return None


# Считает прогресс пользователя: (сколько отвечено, всего вопросов)
async def get_progress(col_idx):
    col_3 = await run_blocking(worksheet.col_values, 3)
    all_questions = col_3[3:]
    total = len([q for q in all_questions if q.strip()])

    user_col = await run_blocking(worksheet.col_values, col_idx)
    user_data = user_col[3:]

    answered = 0
    for i in range(len(all_questions)):
        if not all_questions[i].strip():
            continue
        status = user_data[i].upper() if i < len(user_data) else 'FALSE'
        if status == 'TRUE':
            answered += 1

    return answered, total


# Текст вопроса по номеру строки
async def get_question_text(row):
    cell = await run_blocking(worksheet.cell, row, 3)
    return cell.value


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
        row_3 = await run_blocking(worksheet.row_values, 3)
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

            await run_blocking(sh.batch_update, {"requests": requests})
            if memory_changed:
                await save_memory()
            await log_ds(f"🧹 Удалено людей без роли: {len(requests)}")

        # --- 2. ПРОВЕРКА НИКНЕЙМОВ И ДОБАВЛЕНИЕ НОВИЧКОВ ---
        row_3_updated = await run_blocking(worksheet.row_values, 3)
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
            await run_blocking(worksheet.update_cells, cells_to_update, value_input_option='USER_ENTERED')
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
        all_data = await run_blocking(worksheet.get_all_values)
        if len(all_data) < 3:
            return

        questions = [row[2] for row in all_data[3:] if len(row) > 2 and row[2].strip()]
        num_questions = len(questions)
        if num_questions == 0:
            return

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
            await run_blocking(worksheet.update_cells, cells_to_update, value_input_option='USER_ENTERED')
        if requests:
            await run_blocking(sh.batch_update, {"requests": requests})

        await log_ds("✅ Таблица успешно синхронизирована (добавлены номера, рамки для вопросов и чекбоксы)!")
        return True
    except Exception as e:
        await log_ds(f"❌ Ошибка при синхронизации новых вопросов: {e}")
        return False
