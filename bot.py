import os
import sys
import subprocess
import logging
import random
import string
from pathlib import Path
from datetime import datetime, timedelta, time

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    ConversationHandler,
    ContextTypes
)
from google.oauth2.service_account import Credentials
import gspread

# Настройки (будут переопределены переменными окружения Railway)
TOKEN = os.getenv("TELEGRAM_TOKEN", "7961657553:AAEnDVjmm1QKyhboAtZobv5FupLJDtBPvI8")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1VV_CJ63OlYKiSDupGQp1BilGKHdO8k3Yl3Ti0LZEljk")
CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")  # Полный JSON credentials из переменных окружения

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

REQUIRED_LIBRARIES = [
    'python-telegram-bot[job-queue]',
    'gspread',
    'google-auth',
    'google-auth-oauthlib',
    'google-auth-httplib2'
]

def install_packages():
    """Установка необходимых библиотек"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade"] + REQUIRED_LIBRARIES)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Ошибка установки: {e}")
        return False

def generate_order_number():
    """Генерация номера заказа: 4 буквы + 4 цифры"""
    letters = ''.join(random.choices(string.ascii_uppercase, k=4))
    numbers = ''.join(random.choices(string.digits, k=4))
    return f"{letters}{numbers}"

def parse_price(price_str):
    """Преобразует строку с ценой в float, обрабатывая разные форматы"""
    try:
        cleaned = price_str.replace(',', '.').replace(' ', '')
        return float(cleaned)
    except (ValueError, AttributeError):
        return 0.0

# Состояния диалога
(MAIN_MENU, ORDER_MENU, ORDER_NAME, ORDER_DATE, DELETE_ORDER, MY_ORDERS) = range(6)

class OrderBot:
    def __init__(self):
        self.client = None
        self.sheet = None
        self.menu1 = []
        self.menu2 = []
        self.menu3 = []
        self.id_worksheet = None
        self.init_google_sheets()
        self.initialize_sheets()

    def init_google_sheets(self):
        """Инициализация подключения к Google Sheets"""
        try:
            # Создаем временный файл с credentials из переменной окружения
            if CREDS_JSON:
                import json
                creds_data = json.loads(CREDS_JSON)
                creds = Credentials.from_service_account_info(creds_data)
            else:
                raise Exception("GOOGLE_CREDS_JSON environment variable is not set")
            
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open_by_key(SPREADSHEET_ID)
            logger.info("🔌 Успешное подключение к Google Таблицам")
        except Exception as e:
            logger.error(f"❌ Ошибка Google Sheets: {e}")
            raise

    def initialize_sheets(self):
        """Инициализация листов с заказами и ID"""
        try:
            # Лист для ID пользователей
            try:
                self.id_worksheet = self.sheet.worksheet("ID")
            except gspread.exceptions.WorksheetNotFound:
                self.id_worksheet = self.sheet.add_worksheet(title="ID", rows=1000, cols=1)
                self.id_worksheet.append_row(["Chat ID"])
                logger.info("📋 Лист для ID создан")

            # Листы для заказов
            sheet_titles = ["Заказы Меню1", "Заказы Меню2", "Заказы Меню3"]
            for title in sheet_titles:
                try:
                    worksheet = self.sheet.worksheet(title)
                except gspread.exceptions.WorksheetNotFound:
                    worksheet = self.sheet.add_worksheet(title=title, rows=100, cols=9)

                records = worksheet.get_all_records()
                if not records or len(records[0]) < 9:
                    worksheet.clear()
                    worksheet.append_row([
                        "🔢 Номер заказа", "📅 Дата создания", "⏰ Время создания",
                        "👤 ФИО", "💬 Chat ID", "🍽 Меню", "📆 Дата выкупа",
                        "💰 Стоимость"
                    ])
            logger.info("📋 Все листы инициализированы")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации листов: {e}")
            raise

    def refresh_menu_data(self):
        """Обновление данных меню с обработкой цен"""
        try:
            worksheet = self.sheet.worksheet("Меню")
            self.menu1 = []
            self.menu2 = []
            self.menu3 = []

            menu1_data = worksheet.get_values('A2:B')
            self.menu1 = [
                (item, parse_price(price))
                for item, price in menu1_data
                if item and price
            ]

            menu2_data = worksheet.get_values('C2:D')
            self.menu2 = [
                (item, parse_price(price))
                for item, price in menu2_data
                if item and price
            ]

            menu3_data = worksheet.get_values('E2:F')
            self.menu3 = [
                (item, parse_price(price))
                for item, price in menu3_data
                if item and price
            ]

            logger.info("🔄 Меню успешно обновлено")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки меню: {e}")
            return False

    def save_user_id(self, chat_id):
        """Сохраняет chat_id пользователя в лист ID"""
        try:
            existing_ids = self.id_worksheet.col_values(1)
            if str(chat_id) not in existing_ids:
                self.id_worksheet.append_row([str(chat_id)])
                logger.info(f"✅ Сохранен новый Chat ID: {chat_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения Chat ID: {e}")

    def get_all_users(self):
        """Возвращает все chat_id из листа ID"""
        try:
            return self.id_worksheet.col_values(1)[1:]
        except Exception as e:
            logger.error(f"❌ Ошибка при получении списка пользователей: {e}")
            return []

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        context.user_data.clear()

        keyboard = [
            ['🍽 Сделать заказ', '❌ Удалить заказ'],
            ['📋 Мои заказы']
        ]

        await update.message.reply_text(
            "✨ Добро пожаловать в FoodBot! ✨\n"
            "Выберите действие:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )

        return MAIN_MENU

    async def show_my_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать заказы пользователя на текущую и будущие даты"""
        try:
            chat_id = update.message.chat.id
            today = datetime.now().date()
            orders = []

            for sheet_name in ["Заказы Меню1", "Заказы Меню2", "Заказы Меню3"]:
                try:
                    worksheet = self.sheet.worksheet(sheet_name)
                    records = worksheet.get_all_records()

                    for record in records:
                        if str(record.get("💬 Chat ID")) == str(chat_id):
                            try:
                                order_date = datetime.strptime(record.get("📆 Дата выкупа", ""), "%d.%m.%Y").date()
                                if order_date >= today:
                                    orders.append({
                                        'date': order_date,
                                        'name': record.get("👤 ФИО", "Не указано"),
                                        'menu': record.get("🍽 Меню", "Не указано"),
                                        'order_number': record.get("🔢 Номер заказа", "Не указан"),
                                        'price': record.get("💰 Стоимость", "Не указана")
                                    })
                            except ValueError:
                                continue
                except Exception as e:
                    logger.error(f"❌ Ошибка при чтении листа {sheet_name}: {e}")
                    continue

            if not orders:
                await update.message.reply_text(
                    "У вас нет активных заказов на текущую и будущие даты.",
                    reply_markup=ReplyKeyboardMarkup(
                        [['🍽 Сделать заказ', '❌ Удалить заказ'], ['📋 Мои заказы']],
                        resize_keyboard=True
                    )
                )
                return MAIN_MENU

            # Сортируем заказы по дате
            orders.sort(key=lambda x: x['date'])

            # Формируем сообщение
            message = "📋 Ваши активные заказы:\n\n"
            for order in orders:
                message += (
                    f"👤 ФИО: {order['name']}\n"
                    f"📅 Дата: {order['date'].strftime('%d.%m.%Y')}\n"
                    f"🍽 Меню: {order['menu']}\n"
                    f"💰 Стоимость: {order['price']} ₽\n"
                    f"🔢 Номер: {order['order_number']}\n"
                    f"ℹ️ Столовые приборы и хлеб вы можете приобрести на месте выдачи заказа!🙂\n\n"
                )

            await update.message.reply_text(
                message,
                reply_markup=ReplyKeyboardMarkup(
                    [['🍽 Сделать заказ', '❌ Удалить заказ'], ['📋 Мои заказы']],
                    resize_keyboard=True
                )
            )
            return MAIN_MENU

        except Exception as e:
            logger.error(f"❌ Ошибка при показе заказов: {e}")
            await update.message.reply_text(
                "⚠️ Произошла ошибка при получении ваших заказов. Попробуйте позже.",
                reply_markup=ReplyKeyboardMarkup(
                    [['🍽 Сделать заказ', '❌ Удалить заказ'], ['📋 Мои заказы']],
                    resize_keyboard=True
                )
            )
            return MAIN_MENU

    async def show_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать актуальное меню"""
        try:
            if not self.refresh_menu_data():
                raise Exception("Не удалось обновить данные меню")

            menu1_text = "🍽️ *Меню 1*:\n" + "\n".join(
                f"• {item} - {price:.2f} ₽"
                for item, price in self.menu1
                if item
            )

            menu2_text = "🍲 *Меню 2*:\n" + "\n".join(
                f"• {item} - {price:.2f} ₽"
                for item, price in self.menu2
                if item
            )

            menu3_text = "🍛 *Меню 3*:\n" + "\n".join(
                f"• {item} - {price:.2f} ₽"
                for item, price in self.menu3
                if item
            )

            await update.message.reply_text(
                f"{menu1_text}\n\n{menu2_text}\n\n{menu3_text}",
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardMarkup(
                    [['Меню 1', 'Меню 2', 'Меню 3'], ['↩️ Назад']],
                    resize_keyboard=True
                )
            )

            return ORDER_MENU
        except Exception as e:
            logger.error(f"❌ Ошибка показа меню: {e}")
            await update.message.reply_text(
                "⚠️ Ошибка загрузки меню. Попробуйте позже.",
                reply_markup=ReplyKeyboardMarkup(
                    [['↩️ Назад']],
                    resize_keyboard=True
                )
            )
            return await self.start(update, context)

    async def get_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получить ФИО"""
        text = update.message.text

        if text == '↩️ Назад':
            return await self.start(update, context)

        if text not in ['Меню 1', 'Меню 2', 'Меню 3']:
            await update.message.reply_text(
                "⚠️ Пожалуйста, выберите меню из предложенных вариантов",
                reply_markup=ReplyKeyboardMarkup(
                    [['Меню 1', 'Меню 2', 'Меню 3'], ['↩️ Назад']],
                    resize_keyboard=True
                )
            )
            return ORDER_MENU

        context.user_data['menu'] = text
        price = self.calculate_menu_price(text)
        context.user_data['menu_price'] = f"{price:.2f}"

        await update.message.reply_text(
            "👤 Введите ваши ФИО:",
            reply_markup=ReplyKeyboardMarkup([['↩️ Назад']], resize_keyboard=True)
        )

        return ORDER_NAME

    async def get_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Получить дату выкупа"""
        text = update.message.text

        if text == '↩️ Назад':
            return await self.show_menu(update, context)

        context.user_data['name'] = text

        await update.message.reply_text(
            "📆 Введите дату выкупа заказа (ДД.ММ.ГГГГ):",
            reply_markup=ReplyKeyboardMarkup([['↩️ Назад']], resize_keyboard=True)
        )

        return ORDER_DATE

    async def validate_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Проверка даты"""
        text = update.message.text

        if text == '↩️ Назад':
            return await self.get_name(update, context)

        try:
            date_obj = datetime.strptime(text, "%d.%m.%Y")
            today = datetime.now().date()

            # Проверяем, не пытаются ли сделать заказ на завтра после 12:00
            if date_obj.date() == today + timedelta(days=1) and datetime.now().time() >= time(12, 0):
                await update.message.reply_text(
                    "Прием заявок на обед на завтра - закрыт с 12.00!\n"
                    "Напоминаем, что все заказы на следующий день - до 12 часов текущего дня!",
                    reply_markup=ReplyKeyboardMarkup(
                        [['🍽 Сделать заказ', '❌ Удалить заказ'], ['📋 Мои заказы']],
                        resize_keyboard=True
                    )
                )
                return MAIN_MENU

            if date_obj.date() < today:
                await update.message.reply_text(
                    "⚠️ Дата не может быть в прошлом!",
                    reply_markup=ReplyKeyboardMarkup([['↩️ Назад']], resize_keyboard=True)
                )
                return ORDER_DATE

            context.user_data['order_date'] = text
            return await self.save_order(update, context)

        except ValueError:
            await update.message.reply_text(
                "⚠️ Неверный формат! Введите ДД.ММ.ГГГГ",
                reply_markup=ReplyKeyboardMarkup([['↩️ Назад']], resize_keyboard=True)
            )
            return ORDER_DATE

    async def save_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Сохранить заказ"""
        try:
            user_data = context.user_data
            chat_id = update.message.chat.id

            self.save_user_id(chat_id)

            required_fields = ['name', 'menu', 'order_date', 'menu_price']
            for field in required_fields:
                if field not in user_data:
                    logger.error(f"❌ Отсутствует поле: {field}")
                    raise ValueError(f"Отсутствует обязательное поле: {field}")

            if user_data['menu'] == 'Меню 1':
                worksheet = self.sheet.worksheet("Заказы Меню1")
            elif user_data['menu'] == 'Меню 2':
                worksheet = self.sheet.worksheet("Заказы Меню2")
            else:
                worksheet = self.sheet.worksheet("Заказы Меню3")

            order_number = generate_order_number()

            row_data = [
                order_number,
                datetime.now().strftime("%d.%m.%Y"),
                datetime.now().strftime("%H:%M:%S"),
                user_data['name'],
                str(chat_id),
                user_data['menu'],
                user_data['order_date'],
                user_data['menu_price']
            ]

            worksheet.append_row(row_data)
            logger.info(f"✅ Заказ сохранен: {row_data}")

            await update.message.reply_text(
                f"""
✅ *Заказ №{order_number} сохранен!*

👤 *ФИО:* {user_data['name']}
🍽 *Меню:* {user_data['menu']}
💰 *Стоимость:* {user_data['menu_price']} ₽
📆 *Дата выкупа:* {user_data['order_date']}
🔢 *Номер заказа для удаления:* `{order_number}`

ℹ️ Столовые приборы и хлеб вы можете приобрести на месте выдачи заказа!🙂
                """,
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardMarkup(
                    [['🍽 Сделать заказ', '❌ Удалить заказ'], ['📋 Мои заказы']],
                    resize_keyboard=True
                )
            )

            return MAIN_MENU
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения: {str(e)}")
            await update.message.reply_text(
                "⚠️ Ошибка при сохранении заказа! Попробуйте позже.",
                reply_markup=ReplyKeyboardMarkup(
                    [['🍽 Сделать заказ', '❌ Удалить заказ'], ['📋 Мои заказы']],
                    resize_keyboard=True
                )
            )
            return MAIN_MENU

    async def delete_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Удаление заказа"""
        await update.message.reply_text(
            "🔢 Введите номер заказа для удаления (формат: ABCD1234):",
            reply_markup=ReplyKeyboardMarkup([['↩️ Назад']], resize_keyboard=True)
        )

        return DELETE_ORDER

    async def execute_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Выполнить удаление"""
        try:
            text = update.message.text

            if text == '↩️ Назад':
                return await self.start(update, context)

            order_number = text.upper().strip()
            today = datetime.now().date()
            current_time = datetime.now().time()

            # Проверяем все три листа с заказами
            for sheet_name in ["Заказы Меню1", "Заказы Меню2", "Заказы Меню3"]:
                try:
                    worksheet = self.sheet.worksheet(sheet_name)
                    orders = worksheet.get_all_records()

                    for order in orders:
                        if order.get("🔢 Номер заказа") == order_number:
                            try:
                                order_date = datetime.strptime(order.get("📆 Дата выкупа", ""), "%d.%m.%Y").date()

                                # Проверяем, не пытаются ли удалить заказ на завтра после 12:00
                                if order_date == today + timedelta(days=1) and current_time >= time(12, 0):
                                    await update.message.reply_text(
                                        "Удаление заказов после 12.00 на завтра - запрещено!\n"
                                        "Вы обязаны оплатить ваш заказ.",
                                        reply_markup=ReplyKeyboardMarkup(
                                            [['🍽 Сделать заказ', '❌ Удалить заказ'], ['📋 Мои заказы']],
                                            resize_keyboard=True
                                        )
                                    )
                                    return MAIN_MENU

                                # Если проверка пройдена, удаляем заказ
                                row_num = orders.index(order) + 2  # +1 для заголовка, +1 для индексации с 1
                                worksheet.delete_rows(row_num)

                                await update.message.reply_text(
                                    f"🗑 Заказ №{order_number} успешно удален!",
                                    reply_markup=ReplyKeyboardMarkup(
                                        [['🍽 Сделать заказ', '❌ Удалить заказ'], ['📋 Мои заказы']],
                                        resize_keyboard=True
                                    )
                                )
                                return MAIN_MENU
                            except ValueError:
                                continue
                except Exception as e:
                    logger.error(f"❌ Ошибка при работе с листом {sheet_name}: {e}")
                    continue

            await update.message.reply_text(
                "⚠️ Заказ не найден! Проверьте номер.",
                reply_markup=ReplyKeyboardMarkup(
                    [['🍽 Сделать заказ', '❌ Удалить заказ'], ['📋 Мои заказы']],
                    resize_keyboard=True
                )
            )
            return MAIN_MENU
        except Exception as e:
            logger.error(f"❌ Ошибка удаления: {e}")
            await update.message.reply_text(
                "⚠️ Ошибка при удалении! Попробуйте позже.",
                reply_markup=ReplyKeyboardMarkup(
                    [['🍽 Сделать заказ', '❌ Удалить заказ'], ['📋 Мои заказы']],
                    resize_keyboard=True
                )
            )
            return MAIN_MENU

    def calculate_menu_price(self, menu_name):
        """Вычисление стоимости меню с обработкой ошибок"""
        try:
            if menu_name == 'Меню 1':
                menu = self.menu1
            elif menu_name == 'Меню 2':
                menu = self.menu2
            else:
                menu = self.menu3

            total = sum(price for _, price in menu)
            return round(total, 2)
        except Exception as e:
            logger.error(f"❌ Ошибка расчета стоимости: {e}")
            return 0.0

async def send_morning_message(context: CallbackContext):
    """Ежедневная рассылка сообщений в 6:30 (кроме субботы и воскресенья)"""
    try:
        today = datetime.now().weekday()
        # 5 - суббота, 6 - воскресенье
        if today in [5, 6]:
            logger.info("ℹ️ Сегодня выходной (суббота или воскресенье), рассылка не производится")
            return

        bot = context.application.bot_data['order_bot']
        users = bot.get_all_users()

        if not users:
            logger.info("ℹ️ Нет пользователей для рассылки")
            return

        logger.info(f"🔔 Начинаем рассылку для {len(users)} пользователей")
        success_count = 0
        fail_count = 0

        for chat_id in users:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Доброе утро! Я хочу напомнить о заказах на питание. Хорошего дня!🙂"
                )
                success_count += 1
                logger.info(f"✅ Сообщение отправлено для chat_id {chat_id}")
            except Exception as e:
                fail_count += 1
                logger.error(f"❌ Ошибка отправки для chat_id {chat_id}: {e}")

        logger.info(f"📊 Итоги рассылки: {success_count} успешно, {fail_count} с ошибками")
    except Exception as e:
        logger.error(f"❌ Ошибка в утренней рассылке: {e}")

def main():
    """Основная функция запуска"""
    if not install_packages():
        print("❌ Ошибка установки зависимостей. Смотрите логи")
        return

    try:
        bot = OrderBot()
        app = Application.builder().token(TOKEN).build()
        app.bot_data['order_bot'] = bot

        job_queue = app.job_queue

        # Настраиваем ежедневную рассылку в 6:30 (кроме субботы и воскресенья)
        job_queue.run_daily(
            send_morning_message,
            time=time(hour=6, minute=30),  # 6:30 утра
            days=(0, 1, 2, 3, 4),  # Понедельник - Пятница
            name="morning_message"
        )

        # Настраиваем обработчики команд
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', bot.start)],
            states={
                MAIN_MENU: [
                    MessageHandler(filters.Regex('^🍽 Сделать заказ$'), bot.show_menu),
                    MessageHandler(filters.Regex('^❌ Удалить заказ$'), bot.delete_order),
                    MessageHandler(filters.Regex('^📋 Мои заказы$'), bot.show_my_orders)
                ],
                ORDER_MENU: [MessageHandler(filters.Regex('^(Меню 1|Меню 2|Меню 3|↩️ Назад)$'), bot.get_name)],
                ORDER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.get_date)],
                ORDER_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.validate_date)],
                DELETE_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.execute_delete)]
            },
            fallbacks=[CommandHandler('start', bot.start)]
        )

        app.add_handler(conv_handler)
        logger.info("🤖 Бот запущен и готов к работе!")

        # Для Railway используем webhook или polling в зависимости от окружения
        if os.getenv("RAILWAY_ENVIRONMENT"):
            # Настройка для Railway
            PORT = int(os.getenv("PORT", 8000))
            WEBHOOK_URL = os.getenv("WEBHOOK_URL")
            
            if WEBHOOK_URL:
                # Используем webhook на Railway
                app.run_webhook(
                    listen="0.0.0.0",
                    port=PORT,
                    url_path=TOKEN,
                    webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
                )
            else:
                # Если нет WEBHOOK_URL, используем polling
                app.run_polling()
        else:
            # Локальный запуск с polling
            app.run_polling()

    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}")

if __name__ == '__main__':
    main()
