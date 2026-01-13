"""
API сервер для сохранения и получения результатов анализа
"""
import os
import json
import csv
from datetime import datetime
from typing import List, Dict
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Разрешить CORS для веб-интерфейса

# Путь к файлу для хранения результатов
RESULTS_FILE = "results.json"
RESULTS_DIR = "results"

# Создание директории для результатов, если её нет
os.makedirs(RESULTS_DIR, exist_ok=True)


def load_results() -> List[Dict]:
    """Загрузка результатов из файла"""
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки результатов: {e}")
            return []
    return []


def save_results(results: List[Dict]):
    """Сохранение результатов в файл"""
    try:
        with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения результатов: {e}")


@app.route('/api/results', methods=['POST'])
def save_result():
    """Сохранение результата анализа"""
    try:
        data = request.json
        
        if not data:
            return jsonify({"error": "Нет данных"}), 400
        
        # Загрузка существующих результатов
        results = load_results()
        
        # Добавление нового результата
        results.append(data)
        
        # Сохранение
        save_results(results)
        
        logger.info(f"Результат сохранен для пользователя {data.get('user_info', {}).get('tg_username', 'unknown')}")
        
        return jsonify({"status": "success", "message": "Результат сохранен"}), 200
        
    except Exception as e:
        logger.error(f"Ошибка сохранения результата: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/results', methods=['GET'])
def get_results():
    """Получение всех результатов"""
    try:
        results = load_results()
        
        # Сортировка по дате (новые первыми)
        results.sort(key=lambda x: x.get('user_info', {}).get('timestamp', ''), reverse=True)
        
        # Ограничение количества результатов (опционально)
        limit = request.args.get('limit', type=int)
        if limit:
            results = results[:limit]
        
        return jsonify({"results": results, "count": len(results)}), 200
        
    except Exception as e:
        logger.error(f"Ошибка получения результатов: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/results/<int:index>', methods=['GET'])
def get_result(index: int):
    """Получение конкретного результата по индексу"""
    try:
        results = load_results()
        
        if index < 0 or index >= len(results):
            return jsonify({"error": "Индекс вне диапазона"}), 404
        
        return jsonify(results[index]), 200
        
    except Exception as e:
        logger.error(f"Ошибка получения результата: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Получение статистики"""
    try:
        results = load_results()
        
        stats = {
            "total_analyses": len(results),
            "users_count": len(set(r.get('user_info', {}).get('tg_user_id', 0) for r in results)),
            "recent_analyses": len([r for r in results if is_recent(r)])
        }
        
        return jsonify(stats), 200
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return jsonify({"error": str(e)}), 500


def is_recent(result: Dict, days: int = 7) -> bool:
    """Проверка, является ли результат недавним"""
    try:
        timestamp = result.get('user_info', {}).get('timestamp', '')
        if not timestamp:
            return False
        
        result_date = datetime.fromisoformat(timestamp)
        days_diff = (datetime.now() - result_date).days
        return days_diff <= days
    except:
        return False


@app.route('/')
def index():
    """Главная страница - отдача index.html"""
    return send_from_directory('.', 'index.html')


@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    """Экспорт результатов в CSV"""
    try:
        results = load_results()
        
        if not results:
            return jsonify({"error": "Нет результатов для экспорта"}), 404
        
        # Создание CSV в памяти
        output = []
        output.append([
            'Дата/Время', 'Telegram Username', 'User ID',
            'DSI Score', 'DSI Range', 'Риск ПД',
            'Jitter (%)', 'Shimmer (%)', 'HNR (dB)',
            'F0 Mean (Hz)', 'F0 SD (Hz)', 'Скорость речи (сл/сек)',
            'MPT (сек)', 'F0-High (Hz)', 'I-Low (дБ)',
            'Гипофония', 'Monopitch', 'Monoloudness', 'Охриплость', 'Артикуляция',
            'Длительность (сек)', 'Частота дискретизации'
        ])
        
        for result in results:
            user_info = result.get('user_info', {})
            dsi = result.get('dsi', {})
            features = result.get('features', {})
            symptom_scores = result.get('symptom_scores', {})
            audio_summary = result.get('audio_summary', {})
            
            dsi_breakdown = dsi.get('dsi_breakdown', {})
            
            row = [
                user_info.get('timestamp', ''),
                user_info.get('tg_username', ''),
                user_info.get('tg_user_id', ''),
                dsi.get('dsi_score', ''),
                dsi.get('dsi_range', ''),
                symptom_scores.get('pd_risk', ''),
                features.get('jitter_percent', ''),
                features.get('shimmer_percent', ''),
                features.get('hnr_db', ''),
                features.get('f0_mean_hz', ''),
                features.get('f0_sd_hz', ''),
                features.get('rate_syl_sec', ''),
                dsi_breakdown.get('mpt_sec', ''),
                dsi_breakdown.get('f0_high_hz', ''),
                dsi_breakdown.get('i_low_db', ''),
                symptom_scores.get('hypophonia', ''),
                symptom_scores.get('monopitch', ''),
                symptom_scores.get('monoloudness', ''),
                symptom_scores.get('hoarseness', ''),
                symptom_scores.get('imprecise_articulation', ''),
                audio_summary.get('duration_sec', ''),
                audio_summary.get('sample_rate', '')
            ]
            output.append(row)
        
        # Генерация CSV
        def generate():
            import io
            import csv as csv_module
            
            output_io = io.StringIO()
            writer = csv_module.writer(output_io, delimiter=',', quotechar='"', quoting=csv_module.QUOTE_MINIMAL)
            
            for row in output:
                writer.writerow(row)
            
            output_io.seek(0)
            return output_io.getvalue()
        
        csv_data = generate()
        
        # Создание ответа
        response = Response(
            csv_data.encode('utf-8-sig'),  # UTF-8 BOM для правильного отображения в Excel
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename=parkinson_analysis_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            }
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Ошибка экспорта CSV: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/export/json', methods=['GET'])
def export_json():
    """Экспорт результатов в JSON"""
    try:
        results = load_results()
        
        if not results:
            return jsonify({"error": "Нет результатов для экспорта"}), 404
        
        # Формирование полного отчета
        report = {
            "export_date": datetime.now().isoformat(),
            "total_records": len(results),
            "results": results
        }
        
        json_data = json.dumps(report, ensure_ascii=False, indent=2)
        
        response = Response(
            json_data.encode('utf-8'),
            mimetype='application/json',
            headers={
                'Content-Disposition': f'attachment; filename=parkinson_analysis_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
            }
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Ошибка экспорта JSON: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/export/html', methods=['GET'])
def export_html():
    """Экспорт результатов в HTML отчет"""
    try:
        results = load_results()
        
        if not results:
            return jsonify({"error": "Нет результатов для экспорта"}), 404
        
        # Генерация HTML отчета
        html_content = generate_html_report(results)
        
        response = Response(
            html_content.encode('utf-8'),
            mimetype='text/html',
            headers={
                'Content-Disposition': f'attachment; filename=parkinson_analysis_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
            }
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Ошибка экспорта HTML: {e}")
        return jsonify({"error": str(e)}), 500


def generate_html_report(results: List[Dict]) -> str:
    """Генерация HTML отчета"""
    html = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Отчет анализа голоса на болезнь Паркинсона</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 20px;
            background: #f5f5f5;
        }
        .header {
            background: linear-gradient(135deg, #2c5f8d 0%, #4a90c2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .stats {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            flex: 1;
        }
        .stat-card h3 {
            margin: 0;
            font-size: 2em;
            color: #2c5f8d;
        }
        .stat-card p {
            margin: 5px 0 0 0;
            color: #666;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background: white;
            margin-bottom: 20px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        th {
            background: #2c5f8d;
            color: white;
            padding: 12px;
            text-align: left;
        }
        td {
            padding: 10px;
            border-bottom: 1px solid #ddd;
        }
        tr:hover {
            background: #f5f5f5;
        }
        .badge {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.9em;
        }
        .badge-high { background: #dc3545; color: white; }
        .badge-medium { background: #ffc107; color: #333; }
        .badge-low { background: #28a745; color: white; }
        .result-card {
            background: white;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        .result-header {
            border-bottom: 2px solid #2c5f8d;
            padding-bottom: 10px;
            margin-bottom: 15px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 Отчет анализа голоса на болезнь Паркинсона</h1>
        <p>Дата создания: """ + datetime.now().strftime("%d.%m.%Y %H:%M:%S") + """</p>
        <p>Всего записей: """ + str(len(results)) + """</p>
    </div>
    
    <div class="stats">
        <div class="stat-card">
            <h3>""" + str(len(results)) + """</h3>
            <p>Всего анализов</p>
        </div>
        <div class="stat-card">
            <h3>""" + str(len(set(r.get('user_info', {}).get('tg_user_id', 0) for r in results))) + """</h3>
            <p>Уникальных пользователей</p>
        </div>
        <div class="stat-card">
            <h3>""" + str(len([r for r in results if is_recent(r)])) + """</h3>
            <p>За последние 7 дней</p>
        </div>
    </div>
    
    <h2>Сводная таблица</h2>
    <table>
        <thead>
            <tr>
                <th>Дата/Время</th>
                <th>Username</th>
                <th>DSI Score</th>
                <th>DSI Range</th>
                <th>Риск ПД</th>
                <th>Jitter (%)</th>
                <th>Shimmer (%)</th>
                <th>HNR (dB)</th>
            </tr>
        </thead>
        <tbody>
"""
    
    for result in results:
        user_info = result.get('user_info', {})
        dsi = result.get('dsi', {})
        features = result.get('features', {})
        symptom_scores = result.get('symptom_scores', {})
        
        timestamp = user_info.get('timestamp', '')
        try:
            dt = datetime.fromisoformat(timestamp)
            timestamp = dt.strftime("%d.%m.%Y %H:%M")
        except:
            pass
        
        dsi_score = dsi.get('dsi_score', 'N/A')
        pd_risk = symptom_scores.get('pd_risk', 'N/A')
        
        risk_class = 'badge-low'
        if 'Высокий' in str(pd_risk):
            risk_class = 'badge-high'
        elif 'Умеренный' in str(pd_risk):
            risk_class = 'badge-medium'
        
        html += f"""
            <tr>
                <td>{timestamp}</td>
                <td>{user_info.get('tg_username', 'N/A')}</td>
                <td>{dsi_score if dsi_score != 'N/A' else 'N/A'}</td>
                <td>{dsi.get('dsi_range', 'N/A')}</td>
                <td><span class="badge {risk_class}">{pd_risk}</span></td>
                <td>{features.get('jitter_percent', 'N/A')}</td>
                <td>{features.get('shimmer_percent', 'N/A')}</td>
                <td>{features.get('hnr_db', 'N/A')}</td>
            </tr>
"""
    
    html += """
        </tbody>
    </table>
    
    <h2>Детальные результаты</h2>
"""
    
    for i, result in enumerate(results, 1):
        user_info = result.get('user_info', {})
        dsi = result.get('dsi', {})
        features = result.get('features', {})
        symptom_scores = result.get('symptom_scores', {})
        
        timestamp = user_info.get('timestamp', '')
        try:
            dt = datetime.fromisoformat(timestamp)
            timestamp = dt.strftime("%d.%m.%Y %H:%M:%S")
        except:
            pass
        
        html += f"""
    <div class="result-card">
        <div class="result-header">
            <h3>Запись #{i}: {user_info.get('tg_username', 'N/A')}</h3>
            <p><strong>Дата/Время:</strong> {timestamp}</p>
        </div>
        
        <h4>DSI (Dysphonia Severity Index)</h4>
        <p><strong>Score:</strong> {dsi.get('dsi_score', 'N/A')}</p>
        <p><strong>Range:</strong> {dsi.get('dsi_range', 'N/A')}</p>
        
        <h4>Акустические признаки</h4>
        <ul>
            <li>Jitter: {features.get('jitter_percent', 'N/A')}%</li>
            <li>Shimmer: {features.get('shimmer_percent', 'N/A')}%</li>
            <li>HNR: {features.get('hnr_db', 'N/A')} dB</li>
            <li>F0 Mean: {features.get('f0_mean_hz', 'N/A')} Hz</li>
            <li>F0 SD: {features.get('f0_sd_hz', 'N/A')} Hz</li>
        </ul>
        
        <h4>Оценка симптомов</h4>
        <ul>
            <li>Гипофония: {symptom_scores.get('hypophonia', 'N/A')}</li>
            <li>Monopitch: {symptom_scores.get('monopitch', 'N/A')}</li>
            <li>Monoloudness: {symptom_scores.get('monoloudness', 'N/A')}</li>
            <li>Охриплость: {symptom_scores.get('hoarseness', 'N/A')}</li>
            <li>Артикуляция: {symptom_scores.get('imprecise_articulation', 'N/A')}</li>
        </ul>
        
        <p><strong>Риск ПД:</strong> {symptom_scores.get('pd_risk', 'N/A')}</p>
    </div>
"""
    
    html += """
</body>
</html>
"""
    
    return html


@app.route('/<path:path>')
def serve_static(path):
    """Отдача статических файлов"""
    return send_from_directory('.', path)


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('HOST', '0.0.0.0')
    # В production отключить debug
    debug = os.getenv('DEBUG', 'False').lower() == 'true'
    flask_env = os.getenv('FLASK_ENV', 'development')
    
    logger.info(f"Запуск API сервера на {host}:{port} (env: {flask_env}, debug: {debug})")
    app.run(host=host, port=port, debug=debug)
