"""
Telegram бот для анализа голоса на симптомы болезни Паркинсона
"""
import os
import sys
import asyncio
import logging
import shutil
from datetime import datetime
from typing import Optional
import json

try:
    from aiogram import Bot, Dispatcher, types, F
    from aiogram.filters import Command
    from aiogram.types import Message, Voice, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.fsm.storage.memory import MemoryStorage
    HAS_AIOGRAM = True
except ImportError:
    HAS_AIOGRAM = False
    print("aiogram не установлен. Установите: pip install aiogram")

import requests
from parkinson_analyzer import ParkinsonAnalyzer

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Текст фразы для чтения
READING_TEXT = """Эх, в ясное утро при ярком солнце мы быстро шли по шумной улице, где весёлые дети громко смеялись, а птицы щебетали в зелёных скверах… Вдруг — о чудо! — перед нами возникла огромная рыжая собака, которая радостно виляла пушистым хвостом и тихонько скулила. "Как прекрасен этот мир!" — воскликнул я, чувствуя лёгкий ветерок на лице. Но куда же она так спешит?"""

# Состояния FSM
class AnalysisState(StatesGroup):
    waiting_for_voice = State()


class ParkinsonBot:
    def __init__(self, token: str, api_url: str = "http://localhost:5000"):
        if not HAS_AIOGRAM:
            raise ImportError("aiogram не установлен. Установите: pip install aiogram")
        
        self.bot = Bot(token=token)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.api_url = api_url
        # Создаем analyzer с сохранением сырых данных (по умолчанию)
        self.analyzer = ParkinsonAnalyzer(save_raw_data=True, raw_data_dir="results")
        logger.info("ParkinsonAnalyzer инициализирован с сохранением сырых данных")
        
        # Регистрация handlers
        self.register_handlers()
    
    def register_handlers(self):
        """Регистрация обработчиков команд и сообщений"""
        
        @self.dp.message(Command("start"))
        async def cmd_start(message: Message):
            """Обработка команды /start"""
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Начать анализ", callback_data="start_analysis")],
                [InlineKeyboardButton(text="📋 История отчетов", callback_data="history")],
                [InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")]
            ])
            await message.answer(
                "👋 Добро пожаловать в бот для анализа голоса!\n\n"
                "Я помогу вам проверить голос на симптомы болезни Паркинсона.\n\n"
                "Выберите действие:",
                reply_markup=keyboard
            )
        
        @self.dp.callback_query(F.data == "start_analysis")
        async def callback_start_analysis(callback: CallbackQuery, state: FSMContext):
            """Обработка нажатия кнопки 'Начать анализ'"""
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Начать заново", callback_data="start_analysis")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])
            await callback.message.edit_text(
                f"📝 Пожалуйста, прочитайте следующую фразу:\n\n"
                f"<i>{READING_TEXT}</i>\n\n"
                f"🎤 Отправьте голосовое сообщение или аудио файл после прочтения.\n\n"
                f"<i>Поддерживаемые форматы: голосовые сообщения, .ogg, .wav, .mp3</i>",
                parse_mode="HTML",
                reply_markup=keyboard
            )
            await callback.answer()
            await state.set_state(AnalysisState.waiting_for_voice)
        
        @self.dp.callback_query(F.data == "about")
        async def callback_about(callback: CallbackQuery):
            """Обработка нажатия кнопки 'О боте'"""
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Начать анализ", callback_data="start_analysis")],
                [InlineKeyboardButton(text="📋 История отчетов", callback_data="history")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])
            await callback.message.edit_text(
                "ℹ️ <b>О боте</b>\n\n"
                "Этот бот анализирует голос на симптомы болезни Паркинсона.\n\n"
                "📊 <b>Что анализируется:</b>\n"
                "• DSI (Dysphonia Severity Index)\n"
                "• Акустические признаки (jitter, shimmer, HNR)\n"
                "• Симптомы (гипофония, monopitch, охриплость и др.)\n\n"
                "🎤 <b>Как использовать:</b>\n"
                "1. Нажмите 'Начать анализ'\n"
                "2. Прочитайте предложенный текст\n"
                "3. Отправьте голосовое сообщение или аудио файл\n"
                "4. Получите детальный отчет\n\n"
                "💡 <b>Поддерживаемые форматы:</b>\n"
                "• Голосовые сообщения Telegram\n"
                "• Аудио файлы: .ogg, .wav, .mp3",
                parse_mode="HTML",
                reply_markup=keyboard
            )
            await callback.answer()
        
        @self.dp.callback_query(F.data == "main_menu")
        async def callback_main_menu(callback: CallbackQuery):
            """Обработка нажатия кнопки 'Главное меню'"""
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Начать анализ", callback_data="start_analysis")],
                [InlineKeyboardButton(text="📋 История отчетов", callback_data="history")],
                [InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")]
            ])
            await callback.message.edit_text(
                "👋 Добро пожаловать в бот для анализа голоса!\n\n"
                "Я помогу вам проверить голос на симптомы болезни Паркинсона.\n\n"
                "Выберите действие:",
                reply_markup=keyboard
            )
            await callback.answer()
        
        @self.dp.callback_query(F.data == "history")
        async def callback_history(callback: CallbackQuery):
            """Обработка нажатия кнопки 'История отчетов'"""
            try:
                user_id = callback.from_user.id
                
                # Получение истории пользователя через API
                response = requests.get(
                    f"{self.api_url}/api/results",
                    params={"user_id": user_id},
                    timeout=10
                )
                
                if response.status_code != 200:
                    await callback.answer("❌ Ошибка при получении истории", show_alert=True)
                    return
                
                data = response.json()
                results = data.get('results', [])
                
                if not results:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔍 Начать анализ", callback_data="start_analysis")],
                        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                    ])
                    await callback.message.edit_text(
                        "📋 <b>История отчетов</b>\n\n"
                        "У вас пока нет сохраненных отчетов.\n\n"
                        "Начните новый анализ, чтобы создать первый отчет!",
                        parse_mode="HTML",
                        reply_markup=keyboard
                    )
                    await callback.answer()
                    return
                
                # Показываем список отчетов (первые 10)
                await self._show_history_list(callback, results, user_id, page=0)
                await callback.answer()
                
            except Exception as e:
                logger.error(f"Ошибка получения истории: {e}")
                await callback.answer("❌ Ошибка при получении истории", show_alert=True)
        
        @self.dp.callback_query(F.data.startswith("history_page_"))
        async def callback_history_page(callback: CallbackQuery):
            """Обработка пагинации истории"""
            try:
                page = int(callback.data.split("_")[-1])
                user_id = callback.from_user.id
                
                response = requests.get(
                    f"{self.api_url}/api/results",
                    params={"user_id": user_id},
                    timeout=10
                )
                
                if response.status_code == 200:
                    data = response.json()
                    results = data.get('results', [])
                    await self._show_history_list(callback, results, user_id, page=page)
                
                await callback.answer()
            except Exception as e:
                logger.error(f"Ошибка пагинации истории: {e}")
                await callback.answer("❌ Ошибка", show_alert=True)
        
        @self.dp.callback_query(F.data.startswith("view_report_"))
        async def callback_view_report(callback: CallbackQuery):
            """Просмотр конкретного отчета"""
            try:
                # Получаем индекс отчета из callback_data
                report_index = int(callback.data.split("_")[-1])
                user_id = callback.from_user.id
                
                # Получаем все результаты пользователя
                response = requests.get(
                    f"{self.api_url}/api/results",
                    params={"user_id": user_id},
                    timeout=10
                )
                
                if response.status_code != 200:
                    await callback.answer("❌ Ошибка при получении отчета", show_alert=True)
                    return
                
                data = response.json()
                results = data.get('results', [])
                
                if report_index < 0 or report_index >= len(results):
                    await callback.answer("❌ Отчет не найден", show_alert=True)
                    return
                
                result = results[report_index]
                
                # Отправляем отчет пользователю
                report_parts = self._format_report(result)
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 К истории", callback_data="history")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                ])
                
                # Отправляем первую часть отчета
                if report_parts:
                    await callback.message.edit_text(
                        report_parts[0],
                        parse_mode="HTML",
                        reply_markup=keyboard
                    )
                    
                    # Отправляем остальные части отдельными сообщениями с клавиатурой
                    nav_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📋 К истории", callback_data="history")],
                        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                    ])
                    for part in report_parts[1:]:
                        await callback.message.answer(part, parse_mode="HTML", reply_markup=nav_keyboard)
                        await asyncio.sleep(0.3)
                
                await callback.answer()
                
            except Exception as e:
                logger.error(f"Ошибка просмотра отчета: {e}")
                await callback.answer("❌ Ошибка при просмотре отчета", show_alert=True)
        
        @self.dp.message(Command("analyze"))
        async def cmd_analyze(message: Message, state: FSMContext):
            """Обработка команды /analyze (для обратной совместимости)"""
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Начать заново", callback_data="start_analysis")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])
            await message.answer(
                f"📝 Пожалуйста, прочитайте следующую фразу:\n\n"
                f"<i>{READING_TEXT}</i>\n\n"
                f"🎤 Отправьте голосовое сообщение или аудио файл после прочтения.\n\n"
                f"<i>Поддерживаемые форматы: голосовые сообщения, .ogg, .wav, .mp3</i>",
                parse_mode="HTML",
                reply_markup=keyboard
            )
            await state.set_state(AnalysisState.waiting_for_voice)
        
        @self.dp.message(AnalysisState.waiting_for_voice)
        async def process_voice(message: Message, state: FSMContext):
            """Обработка голосового сообщения или аудио файла"""
            file_id = None
            file_name = None
            file_path = None
            is_voice = False
            
            # Проверка типа файла
            if message.voice:
                # Голосовое сообщение
                voice: Voice = message.voice
                file_id = voice.file_id
                file_name = f"voice_{voice.file_id}.ogg"
                is_voice = True
                
                # Проверка длительности (макс 60 секунд)
                if voice.duration > 60:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="start_analysis")]
                    ])
                    await message.answer(
                        "❌ Голосовое сообщение слишком длинное (максимум 60 секунд). "
                        "Пожалуйста, отправьте более короткое сообщение.",
                        reply_markup=keyboard
                    )
                    return
            elif message.audio:
                # Аудио файл
                audio = message.audio
                file_id = audio.file_id
                file_name = audio.file_name or f"audio_{audio.file_id}"
                
                # Проверка формата
                if file_name:
                    ext = file_name.lower().split('.')[-1]
                    if ext not in ['ogg', 'wav', 'mp3', 'm4a', 'flac']:
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="start_analysis")]
                        ])
                        await message.answer(
                            f"❌ Неподдерживаемый формат файла: .{ext}\n\n"
                            "Поддерживаемые форматы: .ogg, .wav, .mp3, .m4a, .flac",
                            reply_markup=keyboard
                        )
                        return
            elif message.document:
                # Документ (может быть аудио файлом)
                doc = message.document
                file_id = doc.file_id
                file_name = doc.file_name or f"file_{doc.file_id}"
                
                # Проверка формата
                if file_name:
                    ext = file_name.lower().split('.')[-1]
                    if ext not in ['ogg', 'wav', 'mp3', 'm4a', 'flac']:
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="start_analysis")]
                        ])
                        await message.answer(
                            f"❌ Неподдерживаемый формат файла: .{ext}\n\n"
                            "Поддерживаемые форматы: .ogg, .wav, .mp3, .m4a, .flac",
                            reply_markup=keyboard
                        )
                        return
            else:
                # Не голос и не аудио файл
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="start_analysis")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                ])
                await message.answer(
                    "❌ Пожалуйста, отправьте голосовое сообщение или аудио файл.\n\n"
                    "Поддерживаемые форматы: голосовые сообщения, .ogg, .wav, .mp3, .m4a, .flac",
                    reply_markup=keyboard
                )
                return
            
            # Отправка сообщения о начале обработки
            file_type = "голосовое сообщение" if is_voice else "аудио файл"
            processing_msg = await message.answer(f"⏳ Обрабатываю ваш {file_type}...")
            
            try:
                # Скачивание файла
                file_info = await self.bot.get_file(file_id)
                file_path = file_info.file_path
                
                # Скачивание файла
                file_data = await self.bot.download_file(file_path)
                
                # Получение информации о пользователе для генерации ID
                username = message.from_user.username or f"user_{message.from_user.id}"
                user_id = message.from_user.id
                timestamp = datetime.now()
                timestamp_str = timestamp.isoformat()
                
                # Генерация уникального ID для результата
                result_id = f"{user_id}_{timestamp.strftime('%Y%m%d_%H%M%S_%f')[:-3]}"
                
                # Определение расширения файла
                if is_voice:
                    file_ext = "ogg"
                elif file_name:
                    file_ext = file_name.lower().split('.')[-1]
                else:
                    file_ext = "ogg"
                
                # Сохранение во временный файл
                temp_file = f"temp_voice_{result_id}.{file_ext}"
                with open(temp_file, 'wb') as f:
                    f.write(file_data.read())
                
                # Анализ аудио с сохранением сырых данных
                logger.info(f"Начало анализа с сохранением сырых данных, result_id={result_id}")
                result = self.analyzer.analyze_audio_file(temp_file, save_raw=True, result_id=result_id)
                
                # Добавление информации о пользователе в результат
                result['user_info'] = {
                    'tg_username': username,
                    'tg_user_id': user_id,
                    'timestamp': timestamp_str,
                    'reading_text': READING_TEXT
                }
                
                # Проверка сохранения сырых данных
                if 'raw_data' in result and result.get('raw_data'):
                    raw_data_dir = result['raw_data']['data_directory']
                    logger.info(f"✅ Сырые данные сохранены в: {raw_data_dir}")
                    logger.info(f"   Файлы: {list(result['raw_data']['files'].keys())}")
                    
                    # Сохранение исходного файла в директорию сырых данных (если еще не сохранен)
                    original_path = os.path.join(raw_data_dir, "original.ogg")
                    if not os.path.exists(original_path):
                        shutil.copy2(temp_file, original_path)
                        result['raw_data']['files']['original_audio'] = original_path
                        logger.info(f"   Исходный файл скопирован: {original_path}")
                else:
                    logger.warning(f"⚠️  Сырые данные не были сохранены! result_id={result_id}")
                    logger.warning(f"   raw_data в результате: {'raw_data' in result}")
                    if 'raw_data' in result:
                        logger.warning(f"   raw_data значение: {result.get('raw_data')}")
                
                # Сохранение результата через API
                try:
                    save_response = requests.post(
                        f"{self.api_url}/api/results",
                        json=result,
                        timeout=10
                    )
                    if save_response.status_code == 200:
                        logger.info(f"Результат сохранен для пользователя {username}")
                    else:
                        logger.warning(f"Не удалось сохранить результат: {save_response.status_code}")
                except Exception as e:
                    logger.error(f"Ошибка при сохранении результата: {e}")
                
                # Формирование и отправка отчета пользователю
                await processing_msg.delete()
                await self._send_report_to_user(message, result)
                
                # НЕ удаляем временный файл, если сырые данные сохранены
                # (файл уже скопирован в директорию сырых данных)
                if 'raw_data' not in result or not result.get('raw_data'):
                    try:
                        os.remove(temp_file)
                    except:
                        pass
                
                await state.clear()
                
            except Exception as e:
                logger.error(f"Ошибка обработки аудио: {e}")
                await processing_msg.delete()
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="start_analysis")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                ])
                await message.answer(
                    f"❌ Произошла ошибка при обработке аудио: {str(e)}\n\n"
                    "Попробуйте еще раз или используйте кнопки ниже.",
                    reply_markup=keyboard
                )
                await state.clear()
        
    
    def _get_main_keyboard(self) -> InlineKeyboardMarkup:
        """Создание стандартной клавиатуры главного меню"""
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Начать анализ", callback_data="start_analysis")],
            [InlineKeyboardButton(text="📋 История отчетов", callback_data="history")],
            [InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")]
        ])
    
    def _get_navigation_keyboard(self) -> InlineKeyboardMarkup:
        """Создание стандартной клавиатуры навигации"""
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Новый анализ", callback_data="start_analysis")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])
    
    def _get_reference_ranges(self) -> dict:
        """Получение референсных значений для всех показателей"""
        return {
            'dsi_score': {'normal': (2.0, 5.0), 'unit': '', 'name': 'DSI Score'},
            'mpt_sec': {'normal': (15.0, 30.0), 'unit': 'сек', 'name': 'MPT'},
            'f0_high_hz': {'normal': (400.0, 600.0), 'unit': 'Гц', 'name': 'F0-High'},
            'i_low_db': {'normal': (25.0, 45.0), 'unit': 'дБ', 'name': 'I-Low'},
            'jitter_percent': {'normal': (0.0, 1.0), 'unit': '%', 'name': 'Jitter'},
            'shimmer_percent': {'normal': (2.0, 4.0), 'unit': '%', 'name': 'Shimmer'},
            'hnr_db': {'normal': (20.0, 25.0), 'unit': 'дБ', 'name': 'HNR'},
            'f0_mean_hz': {'normal': (100.0, 300.0), 'unit': 'Гц', 'name': 'F0 Mean'},
            'f0_sd_hz': {'normal': (10.0, 50.0), 'unit': 'Гц', 'name': 'F0 SD'},
            'rate_syl_sec': {'normal': (4.5, 7.0), 'unit': 'сл/сек', 'name': 'Скорость речи'},
            'pause_ratio': {'normal': (0.0, 0.30), 'unit': '%', 'name': 'Паузы', 'multiply': 100}
        }
    
    def _format_with_reference(self, value: float, param_name: str, ref_ranges: dict) -> str:
        """Форматирование значения с референсным диапазоном"""
        if param_name not in ref_ranges:
            return f"{value:.2f}"
        
        ref = ref_ranges[param_name]
        unit = ref.get('unit', '')
        normal_min, normal_max = ref['normal']
        
        # Для пауз умножаем на 100 для отображения в процентах
        if ref.get('multiply'):
            value_display = value * ref['multiply']
            # Для сравнения также умножаем нормальные значения
            normal_min_display = normal_min * ref['multiply']
            normal_max_display = normal_max * ref['multiply']
        else:
            value_display = value
            normal_min_display = normal_min
            normal_max_display = normal_max
        
        # Определяем статус
        if normal_min_display <= value_display <= normal_max_display:
            status_emoji = "🟢"
            status_text = "норма"
        elif value_display < normal_min_display:
            status_emoji = "🔴"
            status_text = "ниже нормы"
        else:
            status_emoji = "🔴"
            status_text = "выше нормы"
        
        # Форматируем диапазон
        if ref.get('multiply'):
            range_str = f"{normal_min_display:.1f}-{normal_max_display:.1f}"
        else:
            range_str = f"{normal_min:.1f}-{normal_max:.1f}"
        
        return f"{value_display:.2f} {unit} {status_emoji} (норма: {range_str} {unit})"
    
    def _format_report(self, result: dict) -> list:
        """
        Форматирование отчета для отправки в Telegram
        Возвращает список сообщений (если отчет длинный, разбивается на части)
        """
        messages = []
        
        # Основная информация
        dsi = result.get('dsi', {})
        dsi_score = dsi.get('dsi_score')
        symptom_scores = result.get('symptom_scores', {})
        pd_risk = symptom_scores.get('pd_risk', 'Не определен')
        features = result.get('features', {})
        audio_summary = result.get('audio_summary', {})
        
        # Заголовок
        report_part1 = "✅ <b>Анализ завершен!</b>\n\n"
        report_part1 += "━━━━━━━━━━━━━━━━━━━━\n"
        report_part1 += "📊 <b>ОСНОВНЫЕ РЕЗУЛЬТАТЫ</b>\n"
        report_part1 += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # DSI
        if dsi_score is not None:
            dsi_range = dsi.get('dsi_range', '')
            dsi_breakdown = dsi.get('dsi_breakdown', {})
            
            # Определение эмодзи для DSI
            if dsi_score >= 2.0:
                dsi_emoji = "🟢"
            elif dsi_score >= 0.0:
                dsi_emoji = "🟡"
            elif dsi_score >= -2.0:
                dsi_emoji = "🟠"
            else:
                dsi_emoji = "🔴"
            
            ref_ranges = self._get_reference_ranges()
            
            # DSI с референсом
            dsi_ref = ref_ranges['dsi_score']
            dsi_normal_min, dsi_normal_max = dsi_ref['normal']
            if dsi_normal_min <= dsi_score <= dsi_normal_max:
                dsi_ref_text = f"🟢 (норма: {dsi_normal_min:.1f}-{dsi_normal_max:.1f})"
            else:
                dsi_ref_text = f"🔴 (норма: {dsi_normal_min:.1f}-{dsi_normal_max:.1f})"
            
            report_part1 += f"{dsi_emoji} <b>DSI Score:</b> {dsi_score:.2f} {dsi_ref_text}\n"
            report_part1 += f"📈 <b>Оценка:</b> {dsi_range}\n\n"
            
            report_part1 += "<b>Параметры DSI:</b>\n"
            mpt_val = dsi_breakdown.get('mpt_sec', 0)
            f0_high_val = dsi_breakdown.get('f0_high_hz', 0)
            i_low_val = dsi_breakdown.get('i_low_db', 0)
            jitter_val = dsi_breakdown.get('jitter_percent', 0)
            
            report_part1 += f"  • MPT: {self._format_with_reference(mpt_val, 'mpt_sec', ref_ranges)}\n"
            report_part1 += f"  • F0-High: {self._format_with_reference(f0_high_val, 'f0_high_hz', ref_ranges)}\n"
            report_part1 += f"  • I-Low: {self._format_with_reference(i_low_val, 'i_low_db', ref_ranges)}\n"
            report_part1 += f"  • Jitter: {self._format_with_reference(jitter_val, 'jitter_percent', ref_ranges)}\n\n"
        
        # Риск ПД
        risk_emoji = "🔴" if "Высокий" in pd_risk else "🟡" if "Умеренный" in pd_risk else "🟢"
        report_part1 += f"{risk_emoji} <b>Риск ПД:</b> {pd_risk}\n\n"
        
        messages.append(report_part1)
        
        # Акустические признаки
        report_part2 = "━━━━━━━━━━━━━━━━━━━━\n"
        report_part2 += "🔬 <b>АКУСТИЧЕСКИЕ ПРИЗНАКИ</b>\n"
        report_part2 += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        ref_ranges = self._get_reference_ranges()
        
        report_part2 += f"📊 Jitter: {self._format_with_reference(features.get('jitter_percent', 0), 'jitter_percent', ref_ranges)}\n"
        report_part2 += f"📊 Shimmer: {self._format_with_reference(features.get('shimmer_percent', 0), 'shimmer_percent', ref_ranges)}\n"
        report_part2 += f"📊 HNR: {self._format_with_reference(features.get('hnr_db', 0), 'hnr_db', ref_ranges)}\n"
        report_part2 += f"📊 F0 Mean: {self._format_with_reference(features.get('f0_mean_hz', 0), 'f0_mean_hz', ref_ranges)}\n"
        report_part2 += f"📊 F0 SD: {self._format_with_reference(features.get('f0_sd_hz', 0), 'f0_sd_hz', ref_ranges)}\n"
        report_part2 += f"📊 Скорость речи: {self._format_with_reference(features.get('rate_syl_sec', 0), 'rate_syl_sec', ref_ranges)}\n"
        report_part2 += f"📊 Паузы: {self._format_with_reference(features.get('pause_ratio', 0), 'pause_ratio', ref_ranges)}\n\n"
        
        report_part2 += f"⏱ Длительность: {audio_summary.get('duration_sec', 0):.1f} сек\n"
        report_part2 += f"🎵 Частота дискретизации: {audio_summary.get('sample_rate', 0)} Гц\n\n"
        
        messages.append(report_part2)
        
        # Оценка симптомов
        report_part3 = "━━━━━━━━━━━━━━━━━━━━\n"
        report_part3 += "🏥 <b>ОЦЕНКА СИМПТОМОВ (0-3)</b>\n"
        report_part3 += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        symptom_names = {
            'hypophonia': 'Гипофония',
            'monopitch': 'Monopitch',
            'monoloudness': 'Monoloudness',
            'hoarseness': 'Охриплость',
            'imprecise_articulation': 'Неточная артикуляция'
        }
        
        severity_names = ['Нет', 'Легкий', 'Умеренный', 'Тяжелый']
        
        for key, name in symptom_names.items():
            score = symptom_scores.get(key, 0)
            severity = severity_names[score] if score < len(severity_names) else 'N/A'
            emoji = "🔴" if score >= 2 else "🟡" if score == 1 else "🟢"
            report_part3 += f"{emoji} <b>{name}:</b> {score} ({severity})\n"
        
        messages.append(report_part3)
        
        # Текстовый отчет
        report_items = result.get('report', [])
        if report_items:
            report_part4 = "━━━━━━━━━━━━━━━━━━━━\n"
            report_part4 += "📋 <b>ДЕТАЛЬНЫЙ ОТЧЕТ</b>\n"
            report_part4 += "━━━━━━━━━━━━━━━━━━━━\n\n"
            
            for item in report_items:
                # Пропускаем пустые строки и заголовки DSI
                if item.strip() and not item.startswith('==='):
                    # Форматируем маркеры списка
                    if item.startswith('- '):
                        report_part4 += f"• {item[2:]}\n"
                    elif item.startswith('  -'):
                        report_part4 += f"  • {item[3:]}\n"
                    else:
                        report_part4 += f"{item}\n"
            
            messages.append(report_part4)
        
        # Футер
        report_footer = "\n━━━━━━━━━━━━━━━━━━━━\n"
        report_footer += "💡 <i>Подробные результаты также доступны на веб-сайте</i>"
        
        messages.append(report_footer)
        
        return messages
    
    async def _send_report_to_user(self, message: Message, result: dict):
        """
        Отправка отчета пользователю с разбивкой на части, если нужно
        """
        MAX_MESSAGE_LENGTH = 4000  # Оставляем запас от лимита 4096
        
        try:
            report_parts = self._format_report(result)
            
            # Объединяем части, если они короткие
            combined_parts = []
            current_part = ""
            
            for part in report_parts:
                if len(current_part) + len(part) < MAX_MESSAGE_LENGTH:
                    current_part += part
                else:
                    if current_part:
                        combined_parts.append(current_part)
                    current_part = part
            
            if current_part:
                combined_parts.append(current_part)
            
            # Кнопки для навигации
            keyboard = self._get_navigation_keyboard()
            
            # Отправляем каждую часть с клавиатурой
            for i, part in enumerate(combined_parts):
                await message.answer(part, parse_mode="HTML", reply_markup=keyboard)
                # Небольшая задержка между сообщениями
                if i < len(combined_parts) - 1:
                    await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Ошибка отправки отчета: {e}")
            # Fallback на краткий отчет
            dsi = result.get('dsi', {})
            dsi_score = dsi.get('dsi_score')
            pd_risk = result.get('symptom_scores', {}).get('pd_risk', 'Не определен')
            
            keyboard = self._get_navigation_keyboard()
            
            fallback_text = "✅ Анализ завершен!\n\n"
            if dsi_score is not None:
                fallback_text += f"📊 DSI Score: {dsi_score:.2f}\n"
            fallback_text += f"⚠️ Риск ПД: {pd_risk}\n\n"
            fallback_text += "📋 Подробные результаты доступны на сайте."
            
            await message.answer(fallback_text, parse_mode="HTML", reply_markup=keyboard)
    
    async def _show_history_list(self, callback: CallbackQuery, results: list, user_id: int, page: int = 0):
        """Отображение списка истории отчетов с пагинацией"""
        ITEMS_PER_PAGE = 5
        total_pages = (len(results) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        
        if page < 0:
            page = 0
        if page >= total_pages and total_pages > 0:
            page = total_pages - 1
        
        start_idx = page * ITEMS_PER_PAGE
        end_idx = min(start_idx + ITEMS_PER_PAGE, len(results))
        page_results = results[start_idx:end_idx]
        
        # Формируем текст сообщения
        text = f"📋 <b>История отчетов</b>\n\n"
        text += f"Всего отчетов: {len(results)}\n"
        if total_pages > 0:
            text += f"Страница {page + 1} из {total_pages}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for idx, result in enumerate(page_results):
            global_idx = start_idx + idx
            user_info = result.get('user_info', {})
            dsi = result.get('dsi', {})
            symptom_scores = result.get('symptom_scores', {})
            
            # Форматирование даты
            timestamp = user_info.get('timestamp', '')
            try:
                dt = datetime.fromisoformat(timestamp)
                date_str = dt.strftime("%d.%m.%Y %H:%M")
            except:
                date_str = timestamp
            
            dsi_score = dsi.get('dsi_score')
            pd_risk = symptom_scores.get('pd_risk', 'Не определен')
            
            # Эмодзи для статуса
            if dsi_score is not None:
                if dsi_score >= 2.0:
                    status_emoji = "🟢"
                elif dsi_score >= 0.0:
                    status_emoji = "🟡"
                elif dsi_score >= -2.0:
                    status_emoji = "🟠"
                else:
                    status_emoji = "🔴"
            else:
                status_emoji = "⚪"
            
            text += f"{status_emoji} <b>Отчет #{global_idx + 1}</b>\n"
            text += f"📅 {date_str}\n"
            if dsi_score is not None:
                text += f"📊 DSI: {dsi_score:.2f}\n"
            text += f"⚠️ Риск: {pd_risk}\n\n"
        
        # Формируем кнопки
        keyboard_buttons = []
        
        # Кнопки для каждого отчета на странице
        for idx, result in enumerate(page_results):
            global_idx = start_idx + idx
            user_info = result.get('user_info', {})
            timestamp = user_info.get('timestamp', '')
            try:
                dt = datetime.fromisoformat(timestamp)
                date_str = dt.strftime("%d.%m %H:%M")
            except:
                date_str = f"#{global_idx + 1}"
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"📄 Отчет #{global_idx + 1} ({date_str})",
                    callback_data=f"view_report_{global_idx}"
                )
            ])
        
        # Кнопки пагинации
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"history_page_{page - 1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"history_page_{page + 1}"))
        
        if nav_buttons:
            keyboard_buttons.append(nav_buttons)
        
        # Кнопки навигации
        keyboard_buttons.append([
            InlineKeyboardButton(text="🔍 Новый анализ", callback_data="start_analysis"),
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    
    async def start(self):
        """Запуск бота"""
        logger.info("Запуск Telegram бота...")
        await self.dp.start_polling(self.bot)


def main():
    """Главная функция для запуска бота"""
    # Получение токена из переменной окружения (обязательно)
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    # API_URL должен быть внешним URL для production
    # Для Docker: http://api:5000 (внутренний)
    # Для внешнего доступа: https://yourdomain.com или http://your-ip:5000
    api_url = os.getenv("API_URL", "http://localhost:5000")
    
    if not token:
        print("ОШИБКА: не указан TELEGRAM_BOT_TOKEN")
        print("Установите токен через переменную окружения:")
        print("  export TELEGRAM_BOT_TOKEN='ваш_токен'")
        print("Или создайте файл .env с переменной TELEGRAM_BOT_TOKEN")
        sys.exit(1)
    
    try:
        bot = ParkinsonBot(token, api_url)
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")


if __name__ == "__main__":
    main()
