/*  ═══════════════════════════════════════════
    證券交易模擬遊戲 – Frontend Application
    ═══════════════════════════════════════════ */

const API = '/api/v1';
let TOKEN = localStorage.getItem('token');
let CURRENT_USER = null;
let WS = null;
let priceChart = null;
let candleChart = null;
let kdChart = null;
let currentDetailSymbol = null;
let ohlcHistory = {};  // symbol -> [{time, open, high, low, close, volume}]
let RESET_PW_TARGET_USER_ID = '';
let ALLOW_ADMIN_MODAL_OPEN = false;

// ─── Helpers ───────────────────────────────

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

async function api(method, path, body = null) {
    const opts = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (TOKEN) opts.headers['Authorization'] = `Bearer ${TOKEN}`;
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(API + path, opts);
    if (res.status === 401) { logout(); throw new Error('登入已過期'); }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '操作失敗');
    return data;
}

function toast(msg, type = 'info') {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    $('#toast-container').appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

function fmtMoney(v) {
    const n = Number(v) || 0;
    return '$' + n.toLocaleString('zh-TW', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function fmtPct(v) {
    const n = Number(v) || 0;
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

function fmtTime(ts) {
    if (!ts) return '-';
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return String(ts);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    return `${y}/${m}/${day} ${hh}:${mm}:${ss}`;
}

function closeModal(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('is-open');
    el.hidden = true;
}

function openModal(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.hidden = false;
    el.classList.add('is-open');
}

function withAdminModalPermission(fn) {
    ALLOW_ADMIN_MODAL_OPEN = true;
    try {
        fn();
    } finally {
        setTimeout(() => {
            ALLOW_ADMIN_MODAL_OPEN = false;
        }, 0);
    }
}

function installAdminModalGuard() {
    const guarded = ['add-user-modal', 'edit-user-modal', 'reset-pw-modal'];
    guarded.forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        const obs = new MutationObserver(() => {
            if (!el.hidden && !ALLOW_ADMIN_MODAL_OPEN) {
                el.hidden = true;
            }
        });
        obs.observe(el, { attributes: true, attributeFilter: ['hidden'] });
    });
}

function closeAllModals() {
    document.querySelectorAll('.modal').forEach((el) => {
        el.classList.remove('is-open');
        el.hidden = true;
    });
}

function toggleUserMenu() {
    const dd = $('#user-menu-dropdown');
    if (!dd) return;
    dd.hidden = !dd.hidden;
}

function closeUserMenu() {
    const dd = $('#user-menu-dropdown');
    if (!dd) return;
    dd.hidden = true;
    const sub = $('#system-submenu');
    if (sub) sub.hidden = true;
}

function openChangePasswordModal() {
    $('#pw-old').value = '';
    $('#pw-new').value = '';
    $('#pw-confirm').value = '';
    $('#pw-msg').hidden = true;
    openModal('change-pw-modal');
}

// ─── Clock ─────────────────────────────────

function tickClock() {
    const now = new Date();
    const cl = $('#clock');
    if (cl) cl.textContent = now.toLocaleString('zh-TW', { hour12: false });
}
setInterval(tickClock, 1000);
installAdminModalGuard();

// ─── Auth ──────────────────────────────────

$('#login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const user = $('#login-user').value.trim();
    const pass = $('#login-pass').value;
    try {
        const data = await api('POST', '/auth/login', { username: user, password: pass });
        TOKEN = data.token;
        CURRENT_USER = { user_id: data.user_id, username: data.username, role: data.role, display_name: data.username };
        localStorage.setItem('token', TOKEN);
        showApp();
    } catch (err) {
        $('#login-error').textContent = err.message;
        $('#login-error').hidden = false;
    }
});

function logout() {
    TOKEN = null;
    CURRENT_USER = null;
    localStorage.removeItem('token');
    closeUserMenu();
    closeAllModals();
    $('#app-view').hidden = true;
    $('#login-view').hidden = false;
    $('#login-pass').value = '';
    if (WS) { WS.close(); WS = null; }
}

$('#logout-btn').addEventListener('click', logout);

$('#user-menu-btn')?.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleUserMenu();
});

$('#menu-change-password')?.addEventListener('click', () => {
    closeUserMenu();
    openChangePasswordModal();
});

$('#menu-system-settings')?.addEventListener('click', (e) => {
    e.stopPropagation();
    const sub = $('#system-submenu');
    if (sub) sub.hidden = !sub.hidden;
});

document.querySelectorAll('[data-engine]').forEach(btn => {
    btn.addEventListener('click', async () => {
        const mode = btn.dataset.engine;
        const backendMode = mode === 'market' ? 'A' : 'B';
        const label = mode === 'market'
            ? '撮合引擎A：大盤買賣價 vs 委託'
            : '撮合引擎B：委託 vs 委託';
        try {
            await api('PUT', '/settings/engine-mode', { engine_mode: backendMode });
            localStorage.setItem('matching_engine_mode', mode);
            closeUserMenu();
            toast(`已切換至 ${label}`, 'success');
        } catch (err) {
            toast(`切換失敗: ${err.message}`, 'error');
        }
    });
});

document.addEventListener('click', (e) => {
    const menu = $('#user-menu');
    if (!menu) return;
    if (!menu.contains(e.target)) {
        closeUserMenu();
    }
});

// ─── App Init ──────────────────────────────

async function showApp() {
    closeAllModals();
    $('#login-view').hidden = true;
    $('#app-view').hidden = false;
    $('#user-display').textContent = `👤 ${CURRENT_USER?.display_name || CURRENT_USER?.username || ''}`;

    // Admin sections
    if (CURRENT_USER?.role === 'admin') {
        $('#admin-section').hidden = false;
        $('#tab-admin').hidden = false;
        loadAdminUsers();
    } else {
        $('#admin-section').hidden = true;
        $('#tab-admin').hidden = true;
    }

    connectWebSocket();
    await refreshDashboard();
    await loadMarketData();
    await loadOrders();

    // Sync engine mode: push localStorage value to backend
    const savedMode = localStorage.getItem('matching_engine_mode');
    if (savedMode) {
        const backendMode = savedMode === 'market' ? 'A' : 'B';
        try { await api('PUT', '/settings/engine-mode', { engine_mode: backendMode }); } catch {}
    }
}

// ─── Auto-login ────────────────────────────

(async function autoLogin() {
    closeAllModals();
    if (!TOKEN) return;
    try {
        // Validate token by fetching portfolio
        const data = await api('GET', '/portfolio');
        // If we get here, token is valid; recover user info
        CURRENT_USER = { username: 'user', role: 'user' };
        try {
            // Try to get user info from admin endpoint (works for admin)
            const sysData = await api('GET', '/admin/system');
            if (sysData) CURRENT_USER.role = 'admin';
        } catch { /* not admin */ }
        showApp();
    } catch {
        TOKEN = null;
        localStorage.removeItem('token');
    }
})();

// ─── Navigation ────────────────────────────

$$('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        $$('.nav-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        $$('.panel').forEach(p => { p.classList.remove('active'); p.hidden = true; });
        const panel = $(`#panel-${btn.dataset.panel}`);
        panel.classList.add('active');
        panel.hidden = false;

        // Refresh data on panel switch
        if (btn.dataset.panel === 'dashboard') refreshDashboard();
        if (btn.dataset.panel === 'market') loadMarketData();
        if (btn.dataset.panel === 'trading') loadOrders();
        if (btn.dataset.panel === 'leaderboard') loadLeaderboard();
    });
});

// Sub-tab switching
document.addEventListener('click', (e) => {
    if (!e.target.classList.contains('sub-tab')) return;
    const parent = e.target.closest('.panel') || e.target.closest('#panel-dashboard');
    const tabs = parent.querySelectorAll('.sub-tab');
    const panels = parent.querySelectorAll('.sub-panel');
    tabs.forEach(t => t.classList.remove('active'));
    panels.forEach(p => { p.classList.remove('active'); p.hidden = true; });
    e.target.classList.add('active');
    const target = $(`#sub-${e.target.dataset.sub}`);
    target.classList.add('active');
    target.hidden = false;

    // Load data for sub-panels
    if (e.target.dataset.sub === 'cashflow') loadCashflow();
    if (e.target.dataset.sub === 'holdings') loadHoldings();
    if (e.target.dataset.sub === 'lb-ranking') loadLeaderboard();
    if (e.target.dataset.sub === 'lb-mystocks') loadMyStockRanking();
});

// ─── Dashboard ─────────────────────────────

async function refreshDashboard() {
    try {
        const [portfolio, cashflow, marketData] = await Promise.all([
            api('GET', '/portfolio'),
            api('GET', '/cashflow'),
            api('GET', '/market-data').catch(() => []),
        ]);

        $('#sum-cash').textContent = fmtMoney(portfolio.cash_available);
        $('#sum-locked').textContent = fmtMoney(portfolio.cash_locked);

        // Build market price lookup for holdings valuation
        const marketPrices = {};
        for (const q of (marketData || [])) {
            marketPrices[q.symbol] = Number(q.last_trade_price || q.bid_price || 0);
        }

        let holdingsValue = 0;
        for (const pos of (portfolio.positions || [])) {
            const qty = Number(pos.qty_available) + Number(pos.qty_locked);
            const price = marketPrices[pos.symbol]
                || ohlcHistory[pos.symbol]?.slice(-1)[0]?.close
                || 0;
            holdingsValue += qty * price;
        }
        $('#sum-holdings').textContent = fmtMoney(holdingsValue);

        // Compute yield from actual total deposits (not hardcoded)
        const totalDeposits = (cashflow || [])
            .filter(tx => tx.tx_type === 'DEPOSIT')
            .reduce((sum, tx) => sum + Number(tx.amount), 0);

        const totalAssets = Number(portfolio.cash_available) + Number(portfolio.cash_locked) + holdingsValue;
        const yieldPct = totalDeposits > 0
            ? ((totalAssets - totalDeposits) / totalDeposits * 100)
            : 0;
        $('#sum-yield').textContent = fmtPct(yieldPct);
    } catch (err) {
        console.error('Dashboard refresh failed:', err);
    }
}

// ─── Cash Flow ─────────────────────────────

async function loadCashflow() {
    try {
        const txns = await api('GET', '/cashflow');
        const tbody = $('#cashflow-table tbody');
        tbody.innerHTML = '';
        if (!txns || txns.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:#94a3b8">尚無交易紀錄</td></tr>';
            return;
        }
        for (const tx of txns) {
            const tr = document.createElement('tr');
            const amtClass = Number(tx.amount) >= 0 ? 'cell-up' : 'cell-down';
            tr.innerHTML = `
                <td>${fmtTime(tx.timestamp)}</td>
                <td>${tx.tx_type}</td>
                <td class="${amtClass}">${fmtMoney(tx.amount)}</td>
                <td>${tx.description || ''}</td>
            `;
            tbody.appendChild(tr);
        }
    } catch (err) {
        toast('載入資金明細失敗: ' + err.message, 'error');
    }
}

$('#deposit-btn').addEventListener('click', () => {
    openModal('deposit-modal');
});

$('#deposit-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const amount = Number($('#deposit-amount').value);
    try {
        await api('POST', '/accounts/deposit', { amount });
        closeModal('deposit-modal');
        toast(`入金成功: ${fmtMoney(amount)}`, 'success');
        await refreshDashboard();
        await loadCashflow();
    } catch (err) {
        toast('入金失敗: ' + err.message, 'error');
    }
});

// ─── Holdings ──────────────────────────────

async function loadHoldings() {
    // Fetch portfolio, trades, and market data in parallel
    let portfolio, trades, marketData;
    try {
        [portfolio, trades, marketData] = await Promise.all([
            api('GET', '/portfolio'),
            api('GET', '/trades'),
            api('GET', '/market-data').catch(() => []),
        ]);
    } catch (err) {
        console.error('Load holdings failed:', err);
        return;
    }

    // Build market price lookup
    const marketPrices = {};
    for (const q of (marketData || [])) {
        marketPrices[q.symbol] = Number(q.last_trade_price || q.bid_price || 0);
    }

    // Compute average buy price & first buy date per symbol
    const buyStats = {};  // symbol -> { totalCost, totalQty, firstDate }
    for (const t of (trades || [])) {
        if (t.side !== 'BUY') continue;
        if (!buyStats[t.symbol]) {
            buyStats[t.symbol] = { totalCost: 0, totalQty: 0, firstDate: t.trade_date };
        }
        const s = buyStats[t.symbol];
        s.totalCost += Number(t.price) * Number(t.qty);
        s.totalQty += Number(t.qty);
        if (t.trade_date && t.trade_date < s.firstDate) s.firstDate = t.trade_date;
    }

    // ── Current Holdings ──
    const tbody = $('#holdings-table tbody');
    tbody.innerHTML = '';
    const positions = portfolio.positions || [];
    const activePositions = positions.filter(p => Number(p.qty_available) + Number(p.qty_locked) > 0);

    if (activePositions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#94a3b8">目前無持股</td></tr>';
    } else {
        for (const pos of activePositions) {
            const sym = pos.symbol;
            const qty = Number(pos.qty_available) + Number(pos.qty_locked);
            const stats = buyStats[sym];
            const avgPrice = stats && stats.totalQty > 0 ? (stats.totalCost / stats.totalQty) : 0;
            const buyDate = stats ? stats.firstDate : '-';
            const currentPrice = marketPrices[sym] || 0;
            const marketValue = currentPrice > 0 ? currentPrice * qty : avgPrice * qty;
            const pnl = currentPrice > 0 && avgPrice > 0 ? (currentPrice - avgPrice) * qty : 0;
            const pnlClass = pnl >= 0 ? 'cell-up' : 'cell-down';

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${sym}</strong></td>
                <td>${qty}</td>
                <td>${fmtTime(buyDate)}</td>
                <td>${avgPrice > 0 ? fmtMoney(avgPrice) : '-'}</td>
                <td>${currentPrice > 0 ? fmtMoney(currentPrice) : '-'}</td>
                <td>${fmtMoney(marketValue)}</td>
                <td class="${pnlClass}">${pnl !== 0 ? fmtMoney(pnl) : '-'}</td>
            `;
            tbody.appendChild(tr);
        }
    }

    // ── Trade History (with P&L column) ──
    const tbody2 = $('#trade-history-table tbody');
    tbody2.innerHTML = '';
    if (!trades || trades.length === 0) {
        tbody2.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#94a3b8">尚無成交紀錄</td></tr>';
        return;
    }
    for (const t of trades) {
        const tr = document.createElement('tr');
        const sideClass = t.side === 'BUY' ? 'cell-buy' : 'cell-sell';
        const sideText = t.side === 'BUY' ? '買入' : '賣出';
        let pnlCell = '-';
        if (t.side === 'SELL') {
            const stats = buyStats[t.symbol];
            if (stats && stats.totalQty > 0) {
                const avgBuy = stats.totalCost / stats.totalQty;
                const diff = (Number(t.price) - avgBuy) * Number(t.qty);
                const diffClass = diff >= 0 ? 'cell-up' : 'cell-down';
                pnlCell = `<span class="${diffClass}">${fmtMoney(diff)}</span>`;
            }
        }
        tr.innerHTML = `
            <td>${fmtTime(t.trade_date)}</td>
            <td>${t.symbol}</td>
            <td class="${sideClass}">${sideText}</td>
            <td>${t.qty}</td>
            <td>${fmtMoney(t.price)}</td>
            <td>${pnlCell}</td>
        `;
        tbody2.appendChild(tr);
    }
}

// ─── Password Management ───────────────────

$('#change-pw-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const msg = $('#pw-msg');
    const newPw = $('#pw-new').value;
    if (newPw !== $('#pw-confirm').value) {
        msg.textContent = '新密碼不一致';
        msg.className = 'msg-text err';
        msg.hidden = false;
        return;
    }
    try {
        await api('PUT', '/auth/password', {
            old_password: $('#pw-old').value,
            new_password: newPw,
        });
        msg.textContent = '密碼更新成功 ✓';
        msg.className = 'msg-text ok';
        msg.hidden = false;
        $('#pw-old').value = '';
        $('#pw-new').value = '';
        $('#pw-confirm').value = '';
    } catch (err) {
        msg.textContent = err.message;
        msg.className = 'msg-text err';
        msg.hidden = false;
    }
});

// ─── Admin User Management ─────────────────

async function loadAdminUsers() {
    try {
        const users = await api('GET', '/admin/users');
        const tbody = $('#admin-users-table tbody');
        tbody.innerHTML = '';
        for (const u of users) {
            const tr = document.createElement('tr');
            const roleLabel = u.role === 'admin' ? '👑 管理員' : '👤 使用者';
            tr.innerHTML = `
                <td>${u.username}</td>
                <td>${u.display_name || '-'}</td>
                <td>${roleLabel}</td>
                <td>${fmtTime(u.created_at)}</td>
                <td>
                    <button type="button" class="btn small primary" data-admin-action="edit" data-user-id="${u.user_id}">✏️ 修改</button>
                    <button type="button" class="btn small" data-admin-action="reset" data-user-id="${u.user_id}">🔑 密碼</button>
                    <button type="button" class="btn small danger" data-admin-action="delete" data-user-id="${u.user_id}">🗑️ 刪除</button>
                </td>
            `;
            tbody.appendChild(tr);
        }
    } catch (err) {
        console.error('Load admin users failed:', err);
    }
}

$('#admin-add-user-btn')?.addEventListener('click', (e) => {
    if (!e.isTrusted) return;
    withAdminModalPermission(() => {
        openModal('add-user-modal');
    });
});

$('#admin-refresh-btn')?.addEventListener('click', loadAdminUsers);

$('#admin-users-table')?.addEventListener('click', (e) => {
    // Ignore synthetic/scripted clicks; only real user interaction opens modals.
    if (!e.isTrusted) return;
    const btn = e.target.closest('button[data-admin-action]');
    if (!btn) return;

    const userId = (btn.dataset.userId || '').trim();
    if (!userId) {
        toast('找不到使用者資訊，請重新整理後再試', 'error');
        return;
    }

    const row = btn.closest('tr');
    const username = row?.children?.[0]?.textContent?.trim() || '';
    const displayName = row?.children?.[1]?.textContent?.trim() || '';
    const roleLabel = row?.children?.[2]?.textContent || '';
    const role = roleLabel.includes('管理員') ? 'admin' : 'user';

    const action = btn.dataset.adminAction;
    if (action === 'edit') {
        openEditUser(userId, username, displayName === '-' ? '' : displayName, role);
    } else if (action === 'reset') {
        openResetPassword(userId, username);
    } else if (action === 'delete') {
        deleteUser(userId, username);
    }
});

$('#add-user-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        await api('POST', '/admin/users', {
            username: $('#new-username').value.trim(),
            password: $('#new-password').value,
            display_name: $('#new-display-name').value.trim() || undefined,
            role: $('#new-role').value,
        });
        closeModal('add-user-modal');
        toast('使用者建立成功', 'success');
        loadAdminUsers();
    } catch (err) {
        toast('建立失敗: ' + err.message, 'error');
    }
});

async function deleteUser(userId, username) {
    if (!confirm(`確定要刪除使用者「${username}」嗎？此操作無法復原！`)) return;
    try {
        await api('DELETE', `/admin/users/${userId}`);
        toast(`已刪除使用者: ${username}`, 'success');
        loadAdminUsers();
    } catch (err) {
        toast('刪除失敗: ' + err.message, 'error');
    }
}

function openEditUser(userId, username, displayName, role) {
    $('#edit-user-id').value = userId;
    $('#edit-username').value = username;
    $('#edit-display-name').value = displayName;
    $('#edit-role').value = role;
    withAdminModalPermission(() => {
        openModal('edit-user-modal');
    });
}

$('#edit-user-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const userId = $('#edit-user-id').value;
    const payload = {
        username: $('#edit-username').value.trim(),
        display_name: $('#edit-display-name').value.trim() || null,
        role: $('#edit-role').value,
    };
    try {
        await api('PUT', `/admin/users/${userId}`, payload);
        closeModal('edit-user-modal');
        toast('使用者資料已更新 ✓', 'success');
        loadAdminUsers();
    } catch (err) {
        toast('更新失敗: ' + err.message, 'error');
    }
});

function openResetPassword(userId, username) {
    RESET_PW_TARGET_USER_ID = String(userId || '').trim();
    $('#reset-pw-user-id').value = userId;
    $('#reset-pw-label').textContent = `重設「${username}」的密碼`;
    $('#reset-pw-new').value = '';
    $('#reset-pw-confirm').value = '';
    withAdminModalPermission(() => {
        openModal('reset-pw-modal');
    });
}

$('#reset-pw-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const userId = (RESET_PW_TARGET_USER_ID || $('#reset-pw-user-id').value || '').trim();
    const newPw = $('#reset-pw-new').value;
    if (!userId) {
        toast('密碼重設失敗: 找不到目標使用者，請從使用者清單點選「🔑 密碼」', 'error');
        return;
    }
    if (newPw !== $('#reset-pw-confirm').value) {
        toast('新密碼與確認密碼不一致', 'error');
        return;
    }
    try {
        await api('PUT', `/admin/users/${encodeURIComponent(userId)}/password`, { new_password: newPw });
        closeModal('reset-pw-modal');
        RESET_PW_TARGET_USER_ID = '';
        toast('密碼已重設 ✓', 'success');
    } catch (err) {
        toast('密碼重設失敗: ' + err.message, 'error');
    }
});

// ─── Market Data ───────────────────────────

async function loadMarketData() {
    try {
        const quotes = await api('GET', '/market-data');
        const tbody = $('#market-table tbody');
        tbody.innerHTML = '';
        if (!quotes || quotes.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:#94a3b8">暫無行情資料</td></tr>';
            return;
        }
        for (const q of quotes) {
            const tr = document.createElement('tr');
            const change = Number(q.change) || 0;
            const changePct = Number(q.change_pct) || 0;
            const cls = change >= 0 ? 'cell-up' : 'cell-down';
            const arrow = change >= 0 ? '▲' : '▼';
            tr.innerHTML = `
                <td><strong>${q.symbol}</strong></td>
                <td>${q.name || '-'}</td>
                <td class="${cls}">${q.last_price || q.last_trade_price || '-'}</td>
                <td>${q.bid_price || '-'}</td>
                <td>${q.ask_price || '-'}</td>
                <td class="${cls}">${arrow} ${Math.abs(change).toFixed(2)}</td>
                <td class="${cls}">${fmtPct(changePct)}</td>
                <td>${Number(q.volume || 0).toLocaleString()}</td>
                <td>${q.trades_count ?? '-'}</td>
                <td>
                    <button class="btn small success" onclick="quickBuy('${q.symbol}')">買</button>
                    <button class="btn small danger" onclick="quickSell('${q.symbol}')">賣</button>
                </td>
            `;
            tbody.appendChild(tr);

            // Track OHLC history for charts
            if (!ohlcHistory[q.symbol]) ohlcHistory[q.symbol] = [];
            ohlcHistory[q.symbol].push({
                time: new Date().toLocaleTimeString('zh-TW', { hour12: false, hour: '2-digit', minute: '2-digit' }),
                open: Number(q.open || q.last_trade_price || 0),
                high: Number(q.high || q.last_trade_price || 0),
                low: Number(q.low || q.last_trade_price || 0),
                close: Number(q.close || q.last_trade_price || 0),
                volume: Number(q.volume || 0),
            });
            if (ohlcHistory[q.symbol].length > 120) ohlcHistory[q.symbol].shift();
        }
    } catch (err) {
        console.error('Load market data failed:', err);
    }
}

// Auto-refresh market data every 6 seconds (only when market panel is visible)
setInterval(() => {
    if ($('#panel-market').hidden || !$('#panel-market').classList.contains('active')) return;
    loadMarketData();
}, 6000);

function quickBuy(symbol) {
    // Switch to trading panel and pre-fill symbol
    $$('.nav-btn').forEach(b => b.classList.remove('active'));
    $$('.panel').forEach(p => { p.classList.remove('active'); p.hidden = true; });
    $('[data-panel="trading"]').classList.add('active');
    $('#panel-trading').classList.add('active');
    $('#panel-trading').hidden = false;
    $('#order-symbol').value = symbol;
    document.querySelector('input[name="order-side"][value="BUY"]').checked = true;
}

function quickSell(symbol) {
    quickBuy(symbol);
    document.querySelector('input[name="order-side"][value="SELL"]').checked = true;
}

// Stock search
$('#stock-search-btn').addEventListener('click', searchStock);
$('#stock-search-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') searchStock();
});

async function searchStock() {
    const symbol = $('#stock-search-input').value.trim();
    if (!symbol) return;
    try {
        const [q, histData] = await Promise.all([
            api('GET', `/market-data/${symbol}`),
            api('GET', `/market-data/${symbol}/history?limit=120`),
        ]);
        currentDetailSymbol = symbol;
        $('#stock-detail').hidden = false;
        $('#detail-symbol').textContent = q.symbol;
        $('#detail-name').textContent = q.name || '-';
        $('#detail-price').textContent = q.last_trade_price || '-';
        $('#detail-bid').textContent = q.bid_price || '-';
        $('#detail-ask').textContent = q.ask_price || '-';
        $('#detail-open').textContent = q.open || '-';
        $('#detail-high').textContent = q.high || '-';
        $('#detail-low').textContent = q.low || '-';
        const change = Number(q.change) || 0;
        const changePct = Number(q.change_pct) || 0;
        $('#detail-change').textContent = (change >= 0 ? '+' : '') + change.toFixed(2);
        $('#detail-change').className = 'value ' + (change >= 0 ? 'cell-up' : 'cell-down');
        $('#detail-change-pct').textContent = fmtPct(changePct);
        $('#detail-change-pct').className = 'value ' + (change >= 0 ? 'cell-up' : 'cell-down');
        $('#detail-volume').textContent = Number(q.volume || 0).toLocaleString();
        $('#detail-trades-count').textContent = q.trades_count ?? '-';

        // Store OHLC history
        ohlcHistory[symbol] = (histData.ticks || []).map(t => ({
            time: t.time ? new Date(t.time).toLocaleTimeString('zh-TW', { hour12: false, hour: '2-digit', minute: '2-digit' }) : '',
            open: Number(t.open),
            high: Number(t.high),
            low: Number(t.low),
            close: Number(t.close),
            volume: Number(t.volume),
        }));

        // Render the currently active chart tab
        renderActiveChart(symbol);

        // Wire buy/sell buttons
        $('#detail-buy-btn').onclick = () => quickBuy(symbol);
        $('#detail-sell-btn').onclick = () => quickSell(symbol);
    } catch (err) {
        toast('查無此股票: ' + symbol, 'error');
        $('#stock-detail').hidden = true;
    }
}

// ─── Chart Tab Switching ───────────────────

document.addEventListener('click', (e) => {
    if (!e.target.classList.contains('chart-tab')) return;
    $$('.chart-tab').forEach(t => t.classList.remove('active'));
    e.target.classList.add('active');
    // Show/hide chart containers
    const chart = e.target.dataset.chart;
    $('#chart-line-container').hidden = chart !== 'line';
    $('#chart-candle-container').hidden = chart !== 'candle';
    $('#chart-kd-container').hidden = chart !== 'kd';
    if (currentDetailSymbol) renderActiveChart(currentDetailSymbol);
});

function renderActiveChart(symbol) {
    const activeTab = document.querySelector('.chart-tab.active');
    const chartType = activeTab ? activeTab.dataset.chart : 'line';
    const history = ohlcHistory[symbol] || [];
    if (chartType === 'line') updateLineChart(symbol, history);
    else if (chartType === 'candle') updateCandleChart(symbol, history);
    else if (chartType === 'kd') updateKDChart(symbol, history);
}

// ─── 1. Line Chart (折線圖) ────────────────

function updateLineChart(symbol, history) {
    const ctx = document.getElementById('price-chart');
    if (priceChart) priceChart.destroy();
    if (history.length < 2) { priceChart = null; return; }
    priceChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: history.map(h => h.time),
            datasets: [{
                label: symbol + ' 收盤價',
                data: history.map(h => h.close),
                borderColor: '#2563eb',
                backgroundColor: 'rgba(37,99,235,0.08)',
                fill: true,
                tension: 0.3,
                pointRadius: 1.5,
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: { display: true, ticks: { maxTicksLimit: 12, font: { size: 10 } } },
                y: { display: true, title: { display: true, text: '股價' } },
            },
            plugins: { legend: { display: false }, tooltip: { enabled: true } },
        },
    });
}

// ─── 2. Candlestick + Volume Chart (K線+成交量) ──

function updateCandleChart(symbol, history) {
    const ctx = document.getElementById('candle-chart');
    if (candleChart) candleChart.destroy();
    if (history.length < 2) { candleChart = null; return; }

    // Prepare candlestick-like data using bar chart with floating bars
    const labels = history.map(h => h.time);
    const barColors = history.map(h => h.close >= h.open ? 'rgba(220,38,38,0.85)' : 'rgba(22,163,74,0.85)');
    const borderColors = history.map(h => h.close >= h.open ? '#dc2626' : '#16a34a');
    const wickColors = history.map(h => h.close >= h.open ? '#dc2626' : '#16a34a');

    // Floating bar data: [low, high] for body
    const bodyData = history.map(h => [Math.min(h.open, h.close), Math.max(h.open, h.close)]);
    // Wicks: [low, high] for full range
    const wickData = history.map(h => [h.low, h.high]);
    // Volume (as second y-axis)
    const volumeData = history.map(h => h.volume);
    const volumeColors = history.map(h => h.close >= h.open ? 'rgba(220,38,38,0.3)' : 'rgba(22,163,74,0.3)');

    candleChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                {
                    label: '影線 (High-Low)',
                    data: wickData,
                    backgroundColor: wickColors.map(c => c.replace(')', ',0.5)').replace('rgb', 'rgba')),
                    borderColor: wickColors,
                    borderWidth: 1,
                    barPercentage: 0.1,
                    categoryPercentage: 1.0,
                    yAxisID: 'y',
                    order: 2,
                },
                {
                    label: 'K線本體 (Open-Close)',
                    data: bodyData,
                    backgroundColor: barColors,
                    borderColor: borderColors,
                    borderWidth: 1,
                    barPercentage: 0.6,
                    categoryPercentage: 0.8,
                    yAxisID: 'y',
                    order: 1,
                },
                {
                    label: '成交量',
                    data: volumeData,
                    type: 'bar',
                    backgroundColor: volumeColors,
                    yAxisID: 'y1',
                    barPercentage: 0.5,
                    categoryPercentage: 0.8,
                    order: 3,
                },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: { display: true, ticks: { maxTicksLimit: 12, font: { size: 10 } } },
                y: {
                    type: 'linear', position: 'left',
                    title: { display: true, text: '股價' },
                    grid: { drawOnChartArea: true },
                },
                y1: {
                    type: 'linear', position: 'right',
                    title: { display: true, text: '成交量' },
                    grid: { drawOnChartArea: false },
                    beginAtZero: true,
                },
            },
            plugins: {
                legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            const ds = ctx.dataset.label;
                            const raw = ctx.raw;
                            if (Array.isArray(raw)) return `${ds}: ${raw[0].toFixed(2)} ~ ${raw[1].toFixed(2)}`;
                            return `${ds}: ${Number(raw).toLocaleString()}`;
                        },
                    },
                },
            },
        },
    });
}

// ─── 3. KD Indicator Chart (KD 隨機指標) ──

function calcKD(history, period = 9, kSmooth = 3, dSmooth = 3) {
    // Stochastic Oscillator: %K and %D
    const result = [];
    let prevK = 50, prevD = 50;  // start at 50 (RSV midpoint)

    for (let i = 0; i < history.length; i++) {
        const start = Math.max(0, i - period + 1);
        const slice = history.slice(start, i + 1);
        const highN = Math.max(...slice.map(h => h.high));
        const lowN = Math.min(...slice.map(h => h.low));
        const close = history[i].close;

        // RSV = (close - lowN) / (highN - lowN) * 100
        const rsv = (highN !== lowN) ? ((close - lowN) / (highN - lowN) * 100) : 50;

        // K = 2/3 * prevK + 1/3 * RSV  (exponential smoothing)
        const k = (2 / 3) * prevK + (1 / 3) * rsv;
        // D = 2/3 * prevD + 1/3 * K
        const d = (2 / 3) * prevD + (1 / 3) * k;

        prevK = k;
        prevD = d;

        result.push({ time: history[i].time, k: Math.round(k * 100) / 100, d: Math.round(d * 100) / 100, rsv: Math.round(rsv * 100) / 100 });
    }
    return result;
}

function updateKDChart(symbol, history) {
    const ctx = document.getElementById('kd-chart');
    if (kdChart) kdChart.destroy();
    if (history.length < 9) { kdChart = null; return; }

    const kd = calcKD(history);
    const labels = kd.map(d => d.time);

    kdChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: 'K 值 (快線)',
                    data: kd.map(d => d.k),
                    borderColor: '#2563eb',
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    fill: false,
                },
                {
                    label: 'D 值 (慢線)',
                    data: kd.map(d => d.d),
                    borderColor: '#f59e0b',
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    fill: false,
                },
                {
                    label: 'RSV',
                    data: kd.map(d => d.rsv),
                    borderColor: '#94a3b8',
                    borderWidth: 1,
                    borderDash: [4, 4],
                    pointRadius: 0,
                    tension: 0.3,
                    fill: false,
                },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: { display: true, ticks: { maxTicksLimit: 12, font: { size: 10 } } },
                y: {
                    display: true, min: 0, max: 100,
                    title: { display: true, text: 'KD 值' },
                },
            },
            plugins: {
                legend: { display: true, position: 'top', labels: { boxWidth: 12, font: { size: 11 } } },
                annotation: undefined,  // no annotation plugin needed
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            return `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}`;
                        },
                    },
                },
            },
        },
        plugins: [{
            // Draw overbought/oversold reference lines at 20 and 80
            id: 'kdRefLines',
            beforeDraw(chart) {
                const { ctx: c, chartArea: { left, right }, scales: { y } } = chart;
                c.save();
                c.setLineDash([6, 4]);
                c.lineWidth = 1;
                // Overbought line (80)
                c.strokeStyle = 'rgba(220,38,38,0.5)';
                c.beginPath();
                c.moveTo(left, y.getPixelForValue(80));
                c.lineTo(right, y.getPixelForValue(80));
                c.stroke();
                // Oversold line (20)
                c.strokeStyle = 'rgba(22,163,74,0.5)';
                c.beginPath();
                c.moveTo(left, y.getPixelForValue(20));
                c.lineTo(right, y.getPixelForValue(20));
                c.stroke();
                c.restore();
            },
        }],
    });
}

// ─── Trading ───────────────────────────────

// Toggle price input based on order type
$$('input[name="order-type"]').forEach(radio => {
    radio.addEventListener('change', () => {
        const isMarket = radio.value === 'MARKET' && radio.checked;
        $('#price-group').style.display = isMarket ? 'none' : 'block';
        if (isMarket) $('#order-price').value = '';
    });
});

// Order preview
function updatePreview() {
    const price = Number($('#order-price').value) || 0;
    const qty = Number($('#order-qty').value) || 0;
    const isMarket = document.querySelector('input[name="order-type"]:checked')?.value === 'MARKET';
    if (isMarket || price <= 0 || qty <= 0) {
        $('#order-preview').hidden = true;
        return;
    }
    $('#order-preview').hidden = false;
    $('#preview-total').textContent = fmtMoney(price * qty);
}

$('#order-price')?.addEventListener('input', updatePreview);
$('#order-qty')?.addEventListener('input', updatePreview);

$('#order-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const msg = $('#order-msg');
    const side = document.querySelector('input[name="order-side"]:checked').value;
    const orderType = document.querySelector('input[name="order-type"]:checked').value;
    const symbol = $('#order-symbol').value.trim().toUpperCase();
    const qty = Number($('#order-qty').value);
    const price = orderType === 'MARKET' ? null : Number($('#order-price').value);

    if (!symbol || qty <= 0) {
        msg.textContent = '請填寫完整資訊';
        msg.className = 'msg-text err';
        msg.hidden = false;
        return;
    }

    if (orderType === 'LIMIT' && (!price || price <= 0)) {
        msg.textContent = '限價單須指定價格';
        msg.className = 'msg-text err';
        msg.hidden = false;
        return;
    }

    try {
        const body = { symbol, side, order_type: orderType, qty };
        if (price) body.price = price;
        await api('POST', '/orders', body);
        msg.textContent = '委託送出成功 ✓';
        msg.className = 'msg-text ok';
        msg.hidden = false;
        toast(`${side === 'BUY' ? '買入' : '賣出'} ${symbol} x ${qty} 已送出`, 'success');
        loadOrders();
        setTimeout(() => { msg.hidden = true; }, 3000);
    } catch (err) {
        msg.textContent = err.message;
        msg.className = 'msg-text err';
        msg.hidden = false;
    }
});

async function loadOrders() {
    try {
        const orders = await api('GET', '/orders');
        const tbody = $('#orders-table tbody');
        tbody.innerHTML = '';
        if (!orders || orders.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:#94a3b8">今日尚無委託</td></tr>';
            return;
        }
        for (const o of orders) {
            const tr = document.createElement('tr');
            const sideClass = o.side === 'BUY' ? 'cell-buy' : 'cell-sell';
            const sideText = o.side === 'BUY' ? '買入' : '賣出';
            const typeText = o.order_type === 'MARKET' ? '市價' : '限價';
            const statusMap = {
                PENDING: '待處理',
                ACCEPTED: '已受理',
                ROUTED: '已送撮合',
                PARTIALLY_FILLED: '部分成交',
                FILLED: '全部成交',
                CANCELED: '已撤單',
                CANCELLED: '已撤單',
                REJECTED: '已拒絕',
            };
            const statusDescMap = {
                PENDING: '系統處理中（尚未完成受理）',
                ACCEPTED: '已受理(資金/庫存檢查通過)',
                ROUTED: '正在撮合交易中',
                PARTIALLY_FILLED: '部分成交',
                FILLED: '全部成交',
                CANCELED: '你撤單',
                CANCELLED: '你撤單',
                REJECTED: '委託被拒(買單:現金不足/賣單庫存不足）',
            };
            const statusText = statusMap[o.status] || o.status;
            const statusDesc = statusDescMap[o.status] || '—';
            const canCancel = ['PENDING', 'ACCEPTED', 'ROUTED', 'PARTIALLY_FILLED'].includes(o.status);
            tr.innerHTML = `
                <td>${fmtTime(o.created_at)}</td>
                <td>${o.symbol}</td>
                <td class="${sideClass}">${sideText}</td>
                <td>${typeText}</td>
                <td>${o.price || '-'}</td>
                <td>${o.qty}</td>
                <td>${o.filled_qty || 0}</td>
                <td>${statusText}</td>
                <td>${statusDesc}</td>
                <td>${canCancel ? `<button class="btn small danger" onclick="cancelOrder('${o.order_id}')">撤單</button>` : ''}</td>
            `;
            tbody.appendChild(tr);
        }
    } catch (err) {
        console.error('Load orders failed:', err);
    }
}

async function cancelOrder(orderId) {
    if (!confirm('確定要撤銷此委託嗎？')) return;
    try {
        await api('DELETE', `/orders/${orderId}`);
        toast('委託已撤銷', 'success');
        loadOrders();
    } catch (err) {
        toast('撤單失敗: ' + err.message, 'error');
    }
}

$('#refresh-orders-btn').addEventListener('click', loadOrders);

// ─── Export ────────────────────────────────

$('#export-trades-btn')?.addEventListener('click', async () => {
    window.open(API + '/export/trades?format=csv&token=' + TOKEN, '_blank');
});

$('#export-holdings-btn')?.addEventListener('click', async () => {
    window.open(API + '/export/trades?format=excel&token=' + TOKEN, '_blank');
});

$('#export-orders-btn')?.addEventListener('click', async () => {
    window.open(API + '/export/orders?format=csv&token=' + TOKEN, '_blank');
});

// ─── Leaderboard ───────────────────────────

async function loadLeaderboard() {
    try {
        const data = await api('GET', '/leaderboard');
        const tbody = $('#leaderboard-table tbody');
        tbody.innerHTML = '';
        const rankings = data.rankings || [];
        if (rankings.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#94a3b8">尚無排行資料</td></tr>';
            return;
        }
        for (const r of rankings) {
            const tr = document.createElement('tr');
            const pnl = Number(r.pnl) || 0;
            const pnlPct = Number(r.pnl_pct) || 0;
            const pnlClass = pnl >= 0 ? 'cell-up' : 'cell-down';
            const medal = r.rank === 1 ? '🥇' : r.rank === 2 ? '🥈' : r.rank === 3 ? '🥉' : r.rank;
            const isMe = CURRENT_USER && (r.user_id === CURRENT_USER.user_id || r.username === CURRENT_USER.username);
            tr.style.background = isMe ? '#eff6ff' : '';
            tr.innerHTML = `
                <td><strong>${medal}</strong></td>
                <td>${r.display_name}${isMe ? ' ⭐' : ''}</td>
                <td>${fmtMoney(r.total_assets)}</td>
                <td class="${pnlClass}">${fmtMoney(pnl)}</td>
                <td class="${pnlClass}">${fmtPct(pnlPct)}</td>
                <td>${r.trade_count}</td>
                <td>${fmtMoney(r.trade_volume)}</td>
            `;
            tbody.appendChild(tr);
        }
    } catch (err) {
        console.error('Load leaderboard failed:', err);
    }
}

async function loadMyStockRanking() {
    try {
        const [portfolio, trades, marketData] = await Promise.all([
            api('GET', '/portfolio'),
            api('GET', '/trades'),
            api('GET', '/market-data').catch(() => []),
        ]);

        // Market price lookup
        const marketPrices = {};
        for (const q of (marketData || [])) {
            marketPrices[q.symbol] = Number(q.last_trade_price || q.bid_price || 0);
        }

        // Compute per-stock stats from buy trades
        const stockStats = {};  // symbol -> { totalCost, totalQty }
        for (const t of (trades || [])) {
            if (t.side !== 'BUY') continue;
            if (!stockStats[t.symbol]) stockStats[t.symbol] = { totalCost: 0, totalQty: 0 };
            stockStats[t.symbol].totalCost += Number(t.price) * Number(t.qty);
            stockStats[t.symbol].totalQty += Number(t.qty);
        }

        // Build ranking rows from current positions
        const rows = [];
        for (const pos of (portfolio.positions || [])) {
            const sym = pos.symbol;
            const qty = Number(pos.qty_available) + Number(pos.qty_locked);
            if (qty <= 0) continue;
            const stats = stockStats[sym];
            const avgCost = stats && stats.totalQty > 0 ? stats.totalCost / stats.totalQty : 0;
            const currentPrice = marketPrices[sym] || 0;
            const pnl = currentPrice > 0 && avgCost > 0 ? (currentPrice - avgCost) * qty : 0;
            const pnlPct = avgCost > 0 ? ((currentPrice - avgCost) / avgCost * 100) : 0;
            rows.push({ symbol: sym, qty, avgCost, currentPrice, pnl, pnlPct });
        }

        // Sort by P&L descending
        rows.sort((a, b) => b.pnl - a.pnl);

        const tbody = $('#my-stock-ranking-table tbody');
        tbody.innerHTML = '';
        if (rows.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#94a3b8">目前無持股</td></tr>';
            return;
        }
        rows.forEach((r, i) => {
            const tr = document.createElement('tr');
            const cls = r.pnl >= 0 ? 'cell-up' : 'cell-down';
            tr.innerHTML = `
                <td><strong>${i + 1}</strong></td>
                <td>${r.symbol}</td>
                <td>${r.qty}</td>
                <td>${r.avgCost > 0 ? fmtMoney(r.avgCost) : '-'}</td>
                <td>${r.currentPrice > 0 ? fmtMoney(r.currentPrice) : '-'}</td>
                <td class="${cls}">${fmtMoney(r.pnl)}</td>
                <td class="${cls}">${fmtPct(r.pnlPct)}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error('Load my stock ranking failed:', err);
    }
}

// ─── WebSocket ─────────────────────────────

function connectWebSocket() {
    if (WS) WS.close();
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    // Gap #4: pass JWT token as query param for authentication
    WS = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(TOKEN)}`);

    WS.onopen = () => {
        $('#ws-status').textContent = '🟢 已連線';
        $('#ws-status').className = 'status-dot connected';
        // Subscribe to market data channels (order/trade channels are
        // server-enforced per user_id — no client subscription needed)
        const subscribMsg = {
            action: 'subscribe',
            channels: ['market.*']
        };
        WS.send(JSON.stringify(subscribMsg));
    };

    WS.onclose = () => {
        $('#ws-status').textContent = '🔴 未連線';
        $('#ws-status').className = 'status-dot disconnected';
        // Auto-reconnect
        setTimeout(() => {
            if (TOKEN) connectWebSocket();
        }, 3000);
    };

    WS.onerror = () => {
        $('#ws-status').textContent = '🔴 連線錯誤';
        $('#ws-status').className = 'status-dot disconnected';
    };

    WS.onmessage = (e) => {
        try {
            const event = JSON.parse(e.data);
            handleWSEvent(event);
        } catch { /* ignore */ }
    };
}

function handleWSEvent(evt) {
    const type = evt.event_type || '';

    if (type === 'TradeExecutedEvent') {
        toast(`成交: ${evt.symbol} ${evt.qty}股 @ ${evt.price}`, 'success');
        loadOrders();
        refreshDashboard();
    }

    if (type === 'OrderAcceptedEvent') {
        toast(`委託已受理: ${evt.symbol}`, 'info');
        loadOrders();
    }

    if (type === 'OrderRejectedEvent') {
        toast(`委託被拒: ${evt.reason || ''}`, 'warning');
        loadOrders();
    }

    if (type === 'OrderCancelledEvent') {
        toast('委託已撤銷', 'info');
        loadOrders();
    }

    if (type === 'MarketDataUpdatedEvent') {
        // Update OHLC history for charts
        const sym = evt.symbol;
        if (!ohlcHistory[sym]) ohlcHistory[sym] = [];
        const price = Number(evt.last_price || evt.last_trade_price || 0);
        ohlcHistory[sym].push({
            time: new Date().toLocaleTimeString('zh-TW', { hour12: false, hour: '2-digit', minute: '2-digit' }),
            open: price, high: price, low: price, close: price,
            volume: Number(evt.volume || 0),
        });
        if (ohlcHistory[sym].length > 120) ohlcHistory[sym].shift();

        // If market panel is visible, refresh
        if (!$('#panel-market').hidden) loadMarketData();
    }
}
