/* Shared navigation and authenticated requests; works under /audio_park/. */
(() => {
    const base = new URL('../', document.currentScript.src).pathname.replace(/\/$/, '');
    const nativeFetch = window.fetch.bind(window);
    let csrf = '';
    const isLogin = location.pathname.replace(/\/$/, '').endsWith('/login');
    const api = window.AudioPark = {base, user: null};
    api.url = path => base + path;
    api.escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    api.ready = nativeFetch(api.url(isLogin ? '/api/auth/csrf' : '/api/auth/me'), {credentials: 'same-origin'})
        .then(async response => {
            if (response.status === 401) { location.replace(api.url('/login')); throw new Error('Требуется вход'); }
            if (!response.ok) throw new Error('Не удалось проверить сессию. Обновите страницу');
            const data = await response.json();
            csrf = data.csrf_token;
            api.user = data.user || null;
            return api.user;
        });
    window.fetch = async (input, options = {}) => {
        const url = new URL(typeof input === 'string' ? input : input.url, location.href);
        const local = url.origin === location.origin && url.pathname.startsWith(base + '/api/');
        if (!local) return nativeFetch(input, options);
        await api.ready;
        const headers = new Headers(options.headers || (input instanceof Request ? input.headers : {}));
        const method = (options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
        if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRF-Token', csrf);
        const response = await nativeFetch(input, {...options, headers, credentials:'same-origin'});
        if (response.status === 401 && !isLogin) location.replace(api.url('/login'));
        return response;
    };
    document.addEventListener('DOMContentLoaded', async () => {
        if (isLogin) return;
        try {
            const user = await api.ready;
            const links = [['/', 'Обследование'], ['/results', 'Мои исследования'], ['/guide', 'Инструкция']];
            if (['moderator', 'admin'].includes(user.role)) links.push(['/all-results', 'Все исследования'], ['/users', 'Учётные записи']);
            const nav = document.querySelector('.app-links');
            if (user.role === 'admin') links.push(['/auth-logs', 'Журнал входов']);
            nav.replaceChildren();
            for (const [path, label] of links) {
                const link = document.createElement('a'); link.href = api.url(path); link.textContent = label;
                if (location.pathname.replace(/\/$/, '') === link.pathname.replace(/\/$/, '')) {
                    link.className = 'active'; link.setAttribute('aria-current', 'page');
                }
                nav.append(link);
            }
            const account = document.createElement('div'); account.className = 'account-menu';
            const label = document.createElement('span'); label.textContent = `${user.login} · ${user.role}`;
            const logout = document.createElement('button'); logout.className = 'btn btn-sm btn-outline-secondary'; logout.textContent = 'Выйти';
            logout.addEventListener('click', async () => {
                logout.disabled = true;
                try {
                    const response = await fetch(api.url('/api/auth/logout'), {method:'POST'});
                    if (!response.ok) throw new Error('Не удалось выйти');
                    location.replace(api.url('/login'));
                } catch (error) { logout.disabled = false; alert(error.message); }
            });
            account.append(label, logout); document.querySelector('.app-nav-inner').append(account);
        } catch (error) {
            const alert = document.createElement('p'); alert.className = 'alert alert-danger'; alert.textContent = error.message;
            document.body.prepend(alert);
        }
    });
})();
