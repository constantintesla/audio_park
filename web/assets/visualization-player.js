/* Shared playback clock for every time-based chart, including lazy-loaded charts. */
window.AudioParkPlayer = (() => {
    let player = null;
    let notify = () => {};
    let time = 0;
    let ready = false;
    const el = id => document.getElementById(`player-${id}`);
    const format = value => {
        const seconds = Math.max(0, Math.floor(value || 0));
        return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    };
    function update(value) {
        time = value;
        const duration = player?.getDuration() || 0;
        el('progress').max = duration;
        el('progress').value = value;
        el('time').textContent = `${format(value)} / ${format(duration)}`;
        notify(value);
    }
    function seek(value) {
        if (!ready || !Number.isFinite(value)) return;
        player.setTime(Math.max(0, Math.min(player.getDuration(), value)));
        update(player.getCurrentTime());
    }
    function setEnabled(enabled) {
        for (const id of ['play', 'back', 'forward', 'progress']) el(id).disabled = !enabled;
    }
    function reset() {
        player = null;
        ready = false;
        notify = () => {};
        document.getElementById('sticky-player').hidden = false;
        document.body.classList.add('has-sticky-player');
        el('status').textContent = 'Загрузка аудио...';
        el('play').textContent = 'Воспроизвести';
        document.getElementById('waveform').replaceChildren();
        setEnabled(false);
        update(0);
    }
    function fail(error) {
        ready = false;
        setEnabled(false);
        el('status').textContent = `Не удалось загрузить запись: ${error.message}`;
    }
    function bind(instance, onTime) {
        player = instance;
        notify = onTime;
        const active = handler => (...args) => { if (player === instance) handler(...args); };
        instance.setVolume(Number(el('volume').value));
        instance.on('ready', active(() => {
            ready = true;
            setEnabled(true);
            el('status').textContent = '';
            update(instance.getCurrentTime());
        }));
        instance.on('timeupdate', active(update));
        instance.on('seeking', active(update));
        instance.on('play', active(() => { el('play').textContent = 'Пауза'; }));
        instance.on('pause', active(() => { el('play').textContent = 'Воспроизвести'; }));
        instance.on('finish', active(() => {
            el('play').textContent = 'Воспроизвести';
            update(instance.getDuration());
        }));
        instance.on('error', active(fail));
    }
    const playhead = {
        id: 'audioParkPlayhead',
        afterDraw(chart) {
            const {ctx, chartArea: area, scales: {x}} = chart;
            if (!area || !x) return;
            const px = x.getPixelForValue(time);
            if (!Number.isFinite(px) || px < area.left || px > area.right) return;
            ctx.save();
            ctx.beginPath();
            ctx.rect(area.left, area.top, area.right - area.left, area.bottom - area.top);
            ctx.clip();
            ctx.beginPath();
            ctx.moveTo(px, area.top);
            ctx.lineTo(px, area.bottom);
            ctx.strokeStyle = '#b42318';
            ctx.lineWidth = 2;
            ctx.stroke();
            ctx.restore();
        }
    };
    if (window.Chart) Chart.register(playhead);
    function attachChart(chart) {
        // A native listener also works for the spectrogram, whose Chart.js events are disabled.
        if (chart.$audioParkAttached) return;
        chart.$audioParkAttached = true;
        chart.canvas.onclick = event => {
            const point = Chart.helpers.getRelativePosition(event, chart);
            const area = chart.chartArea;
            if (point.x < area.left || point.x > area.right || point.y < area.top || point.y > area.bottom) return;
            seek(chart.scales.x.getValueForPixel(point.x));
        };
        chart.draw();
    }
    window.addEventListener('DOMContentLoaded', () => {
        el('play').onclick = () => {
            if (ready) player.playPause().catch(error => { el('status').textContent = error.message; });
        };
        el('back').onclick = () => seek(time - 10);
        el('forward').onclick = () => seek(time + 10);
        el('progress').oninput = event => seek(Number(event.target.value));
        el('volume').oninput = event => player?.setVolume(Number(event.target.value));
        const panel = document.getElementById('sticky-player');
        new ResizeObserver(() => {
            document.body.style.setProperty('--sticky-player-height', `${panel.getBoundingClientRect().height}px`);
        }).observe(panel);
    });
    return {reset, bind, fail, seek, attachChart, drawCharts(charts, value) {
        time = value;
        Object.values(charts).forEach(chart => chart.draw());
    }};
})();
