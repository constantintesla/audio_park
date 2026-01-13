"""
Telegram бот для анализа голоса на симптомы болезни Паркинсона
"""
import os
import asyncio
import logging
from datetime import datetime
from typing import Optional
import json

try:
    from aiogram import Bot, Dispatcher, types, F
    from aiogram.filters import Command
    from aiogram.types import Message, Voice
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
        self.analyzer = ParkinsonAnalyzer()
        
        # Регистрация handlers
        self.register_handlers()
    
    def register_handlers(self):
        """Регистрация обработчиков команд и сообщений"""
        
        @self.dp.message(Command("start"))
        async def cmd_start(message: Message):
            """Обработка команды /start"""
            await message.answer(
                "👋 Добро пожаловать в бот для анализа голоса!\n\n"
                "Я помогу вам проверить голос на симптомы болезни Паркинсона.\n\n"
                "Используйте команду /analyze для начала анализа."
            )
        
        @self.dp.message(Command("analyze"))
        async def cmd_analyze(message: Message, state: FSMContext):
            """Обработка команды /analyze"""
            await message.answer(
                f"📝 Пожалуйста, прочитайте следующую фразу:\n\n"
                f"<i>{READING_TEXT}</i>\n\n"
                f"🎤 Отправьте голосовое сообщение после прочтения.",
                parse_mode="HTML"
            )
            await state.set_state(AnalysisState.waiting_for_voice)
        
        @self.dp.message(AnalysisState.waiting_for_voice)
        async def process_voice(message: Message, state: FSMContext):
            """Обработка голосового сообщения"""
            # Проверка наличия голосового сообщения
            if not message.voice:
                await message.answer(
                    "❌ Пожалуйста, отправьте голосовое сообщение.\n\n"
                    "Используйте команду /analyze для начала нового анализа."
                )
                return
            
            voice: Voice = message.voice
            
            # Проверка длительности (макс 60 секунд)
            if voice.duration > 60:
                await message.answer(
                    "❌ Голосовое сообщение слишком длинное (максимум 60 секунд). "
                    "Пожалуйста, отправьте более короткое сообщение."
                )
                return
            
            # Отправка сообщения о начале обработки
            processing_msg = await message.answer("⏳ Обрабатываю ваше голосовое сообщение...")
            
            try:
                # Скачивание голосового файла
                file_info = await self.bot.get_file(voice.file_id)
                file_path = file_info.file_path
                
                # Скачивание файла
                file_data = await self.bot.download_file(file_path)
                
                # Сохранение во временный файл
                temp_file = f"temp_voice_{message.from_user.id}_{datetime.now().timestamp()}.ogg"
                with open(temp_file, 'wb') as f:
                    f.write(file_data.read())
                
                # Анализ аудио
                result = self.analyzer.analyze_audio_file(temp_file)
                
                # Получение информации о пользователе
                username = message.from_user.username or f"user_{message.from_user.id}"
                user_id = message.from_user.id
                timestamp = datetime.now().isoformat()
                
                # Добавление информации о пользователе в результат
                result['user_info'] = {
                    'tg_username': username,
                    'tg_user_id': user_id,
                    'timestamp': timestamp,
                    'reading_text': READING_TEXT
                }
                
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
                
                # Удаление временного файла
                try:
                    os.remove(temp_file)
                except:
                    pass
                
                await state.clear()
                
            except Exception as e:
                logger.error(f"Ошибка обработки голоса: {e}")
                await processing_msg.delete()
                await message.answer(
                    f"❌ Произошла ошибка при обработке голосового сообщения: {str(e)}\n\n"
                    "Попробуйте еще раз, отправив команду /analyze"
                )
                await state.clear()
        
    
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
            
            report_part1 += f"{dsi_emoji} <b>DSI Score:</b> {dsi_score:.2f}\n"
            report_part1 += f"📈 <b>Оценка:</b> {dsi_range}\n\n"
            
            report_part1 += "<b>Параметры DSI:</b>\n"
            report_part1 += f"  • MPT: {dsi_breakdown.get('mpt_sec', 0):.2f} сек\n"
            report_part1 += f"  • F0-High: {dsi_breakdown.get('f0_high_hz', 0):.1f} Гц\n"
            report_part1 += f"  • I-Low: {dsi_breakdown.get('i_low_db', 0):.1f} дБ\n"
            report_part1 += f"  • Jitter: {dsi_breakdown.get('jitter_percent', 0):.2f}%\n\n"
        
        # Риск ПД
        risk_emoji = "🔴" if "Высокий" in pd_risk else "🟡" if "Умеренный" in pd_risk else "🟢"
        report_part1 += f"{risk_emoji} <b>Риск ПД:</b> {pd_risk}\n\n"
        
        messages.append(report_part1)
        
        # Акустические признаки
        report_part2 = "━━━━━━━━━━━━━━━━━━━━\n"
        report_part2 += "🔬 <b>АКУСТИЧЕСКИЕ ПРИЗНАКИ</b>\n"
        report_part2 += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        report_part2 += f"📊 Jitter: {features.get('jitter_percent', 0):.2f}%\n"
        report_part2 += f"📊 Shimmer: {features.get('shimmer_percent', 0):.2f}%\n"
        report_part2 += f"📊 HNR: {features.get('hnr_db', 0):.1f} дБ\n"
        report_part2 += f"📊 F0 Mean: {features.get('f0_mean_hz', 0):.1f} Гц\n"
        report_part2 += f"📊 F0 SD: {features.get('f0_sd_hz', 0):.1f} Гц\n"
        report_part2 += f"📊 Скорость речи: {features.get('rate_syl_sec', 0):.1f} сл/сек\n"
        report_part2 += f"📊 Паузы: {features.get('pause_ratio', 0)*100:.1f}%\n\n"
        
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
        report_footer += "💡 <i>Подробные результаты также доступны на веб-сайте</i>\n"
        report_footer += "🔄 Для нового анализа отправьте /analyze"
        
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
            
            # Отправляем каждую часть
            for i, part in enumerate(combined_parts):
                if i == 0:
                    # Первое сообщение отправляем как ответ
                    await message.answer(part, parse_mode="HTML")
                else:
                    # Остальные отправляем отдельными сообщениями
                    await message.answer(part, parse_mode="HTML")
                    # Небольшая задержка между сообщениями
                    await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Ошибка отправки отчета: {e}")
            # Fallback на краткий отчет
            dsi = result.get('dsi', {})
            dsi_score = dsi.get('dsi_score')
            pd_risk = result.get('symptom_scores', {}).get('pd_risk', 'Не определен')
            
            fallback_text = "✅ Анализ завершен!\n\n"
            if dsi_score is not None:
                fallback_text += f"📊 DSI Score: {dsi_score:.2f}\n"
            fallback_text += f"⚠️ Риск ПД: {pd_risk}\n\n"
            fallback_text += "📋 Подробные результаты доступны на сайте."
            
            await message.answer(fallback_text, parse_mode="HTML")
    
    async def start(self):
        """Запуск бота"""
        logger.info("Запуск Telegram бота...")
        await self.dp.start_polling(self.bot)


def main():
    """Главная функция для запуска бота"""
    # Получение токена из переменной окружения или использование дефолтного
    token = os.getenv("TELEGRAM_BOT_TOKEN", "8365860763:AAEPKawMwP4KC_qYE1qcSvi2v2cg2SpUXg8")
    # API_URL должен быть внешним URL для production
    # Для Docker: http://api:5000 (внутренний)
    # Для внешнего доступа: https://yourdomain.com или http://your-ip:5000
    api_url = os.getenv("API_URL", "http://localhost:5000")
    
    if not token:
        print("Ошибка: не указан TELEGRAM_BOT_TOKEN")
        print("Установите токен: export TELEGRAM_BOT_TOKEN='ваш_токен'")
        return
    
    try:
        bot = ParkinsonBot(token, api_url)
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")


if __name__ == "__main__":
    main()
