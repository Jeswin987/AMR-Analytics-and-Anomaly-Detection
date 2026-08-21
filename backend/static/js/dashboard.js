/**
 * AMR Fleet Analytics Dashboard — Frontend Logic
 * Mission drill-down: path visualizer, error panel, speed chart, mode timeline.
 */

// ── Application State ────────────────────────────────────────────────────────
const state = {
    charts:          {},
    currentDeviceId: null,
    currentData:     null,
    loadingDevice:   false,
};

// ── Safe DOM setter — prevents "Cannot set properties of null" crashes ────────
function setHTML(selector, html, useId = false) {
    const el = useId
        ? document.getElementById(selector)
        : document.querySelector(selector);
    if (el) el.innerHTML = html;
    else console.warn(`[setHTML] Element not found: ${selector}`);
}

// ── Loading Overlay ───────────────────────────────────────────────────────────
function showLoading(on) {
    document.getElementById('loadingOverlay').classList.toggle('active', on);
}

// ── Tab Switcher ──────────────────────────────────────────────────────────────
function switchTab(tabName, btn) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    if (btn) btn.classList.add('active');
    document.getElementById(tabName).classList.add('active');
}

// ── Formatters ────────────────────────────────────────────────────────────────
function getStatusClass(status) {
    if (!status) return 'status-warning';
    const s = status.toUpperCase();
    if (s === 'COMPLETED') return 'status-good';
    if (s === 'FAILED' || s === 'CANCELLED') return 'status-critical';
    if (s === 'EXECUTING' || s === 'RUNNING') return 'status-running';
    return 'status-warning';
}

function fmtTimestamp(ts) {
    if (!ts) return 'N/A';
    try {
        return new Date(ts).toLocaleString('en-US', {
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
        });
    } catch (e) { return ts; }
}

function fmtDistance(val) {
    if (val === null || val === undefined || val === '') return '0';
    return parseFloat(val).toFixed(2);
}

function fmtSpeed(v) {
    if (v === null || v === undefined || v === '') return '';
    const n = parseFloat(v);
    return !isNaN(n) ? n.toFixed(2) : String(v);
}

function fmtDriving(v) {
    if (v === null || v === undefined || v === '') return '';
    const n = parseFloat(v);
    if (!isNaN(n)) return n > 0 ? 'true' : 'false';
    return String(v);
}

// ── Chart Management ──────────────────────────────────────────────────────────
function destroyChart(id) {
    if (state.charts[id]) {
        state.charts[id].destroy();
        delete state.charts[id];
    }
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────
async function init() {
    try {
        const res     = await fetch('/api/devices');
        const json    = await res.json();
        const devices = json.devices || [];
        const sel     = document.getElementById('deviceSelector');
        sel.innerHTML = '<option value="">Select Robot...</option>';
        devices.forEach(device => {
            const o = document.createElement('option');
            // value = UUID (used for API calls), text = human-readable name
            o.value       = device.id   || device;
            o.textContent = device.name || device.id || device;
            sel.appendChild(o);
        });
        if (devices.length > 0) {
            sel.value = devices[0].id || devices[0];
            await loadDevice(devices[0].id || devices[0]);
        } else {
            document.getElementById('summaryCards').innerHTML =
                '<div class="no-data">⚠️ No devices found. Check your PostgreSQL connection.</div>';
        }
    } catch (e) {
        document.getElementById('summaryCards').innerHTML =
            '<div class="no-data">⚠️ Failed to connect to server. Is run.py running?</div>';
    }
}

// ── Device Loading ────────────────────────────────────────────────────────────
async function loadDevice(deviceId, forceRefresh = false) {
    if (state.loadingDevice) return;
    state.loadingDevice = true;
    showLoading(true);
    try {
        const startDate = document.getElementById('startDate').value;
        const endDate   = document.getElementById('endDate').value;
        const params    = [];
        if (forceRefresh) params.push('refresh=true');
        if (startDate)    params.push(`start_date=${startDate}`);
        if (endDate)      params.push(`end_date=${endDate}`);
        const qs  = params.length ? '?' + params.join('&') : '';
        const res  = await fetch(`/api/data/${encodeURIComponent(deviceId)}${qs}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        state.currentDeviceId = deviceId;
        state.currentData     = data;
        populateMissionSelector(data.mission_rows || []);
        renderOverview(data);
        const lbl = document.getElementById('dateRangeLabel');
        if (startDate && endDate) {
            lbl.textContent    = `📅 ${startDate} → ${endDate}`;
            lbl.style.display  = 'inline-block';
        } else {
            lbl.style.display = 'none';
        }
    } catch (e) {
        setHTML('summaryCards', `<div class="no-data">⚠️ ${e.message}</div>`, true);
    } finally {
        state.loadingDevice = false;
        showLoading(false);
    }
}

function loadDateRange() {
    const deviceId  = document.getElementById('deviceSelector').value;
    const startDate = document.getElementById('startDate').value;
    const endDate   = document.getElementById('endDate').value;
    if (!deviceId)              { alert('Select a device first.'); return; }
    if (!startDate || !endDate) { alert('Set both a start and end date.'); return; }
    if (startDate > endDate)    { alert('Start date must be before end date.'); return; }
    document.getElementById('missionSelector').value = 'ALL';
    loadDevice(deviceId, true);
}

function clearDateRange() {
    document.getElementById('startDate').value = '';
    document.getElementById('endDate').value   = '';
    document.getElementById('dateRangeLabel').style.display = 'none';
    const deviceId = document.getElementById('deviceSelector').value;
    if (deviceId) {
        document.getElementById('missionSelector').value = 'ALL';
        loadDevice(deviceId, true);
    }
}

async function onDeviceChange() {
    const deviceId = document.getElementById('deviceSelector').value;
    if (!deviceId) return;
    document.getElementById('missionSelector').value = 'ALL';
    await loadDevice(deviceId);
}

async function refreshDevice() {
    const deviceId = document.getElementById('deviceSelector').value;
    if (!deviceId) return;
    showLoading(true);
    try {
        const startDate = document.getElementById('startDate').value;
        const endDate   = document.getElementById('endDate').value;
        const params    = ['refresh=true'];
        if (startDate) params.push(`start_date=${startDate}`);
        if (endDate)   params.push(`end_date=${endDate}`);
        const res  = await fetch(`/api/data/${encodeURIComponent(deviceId)}?${params.join('&')}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        state.currentDeviceId = deviceId;
        state.currentData     = data;
        populateMissionSelector(data.mission_rows || []);
        renderOverview(data);
    } catch (e) {
        alert('Refresh failed: ' + e.message);
    } finally {
        showLoading(false);
    }
}

// ── Mission Selector ──────────────────────────────────────────────────────────
function populateMissionSelector(missions) {
    const sel = document.getElementById('missionSelector');
    sel.innerHTML = '<option value="ALL">All Missions (Device Overview)</option>';
    missions.forEach((m, i) => {
        const o = document.createElement('option');
        o.value = i;
        let startLabel = '';
        if (m.startedAt) {
            try {
                const d = new Date(m.startedAt);
                startLabel = ' — ' + d.toLocaleString('en-US', {
                    month: 'short', day: 'numeric',
                    hour: '2-digit', minute: '2-digit', hour12: true,
                });
            } catch (_) {}
        }
        let anomalyFlag = '';
        if (m.startedAt && m.completedAt) {
            const durationH = (new Date(m.completedAt) - new Date(m.startedAt)) / 3_600_000;
            if (durationH >= 24) anomalyFlag = ' ⚠️';
        }
        o.textContent = `${i + 1}. ${m.missionName || 'Mission ' + (i + 1)}${anomalyFlag} [${(m.status || 'unknown').toLowerCase()}]${startLabel}`;
        sel.appendChild(o);
    });
}

async function onMissionChange() {
    const val = document.getElementById('missionSelector').value;
    if (val === 'ALL') {
        if (state.currentData) renderOverview(state.currentData);
        return;
    }
    showLoading(true);
    try {
        const res  = await fetch(`/api/mission/${encodeURIComponent(state.currentDeviceId)}/${parseInt(val)}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        renderMission(data);
    } catch (e) {
        alert('Error loading mission: ' + e.message);
    } finally {
        showLoading(false);
    }
}

// ── Overview Render ───────────────────────────────────────────────────────────
function renderOverview(data) {
    document.getElementById('viewModeBadge').textContent = '📊 Device Overview';
    document.getElementById('missionInfoCard').style.display = 'none';
    document.getElementById('utilizationChartsGrid').style.display = 'grid';
    document.getElementById('performanceChartsGrid').style.display = 'grid';
    document.getElementById('batteryChartsGrid').style.display = 'grid';
    document.getElementById('utilizationContent').style.display = 'none';
    document.getElementById('utilizationTable').style.display = 'table';
    document.getElementById('batteryContent').style.display = 'none';
    document.getElementById('batteryTable').style.display = 'table';
    document.getElementById('utilizationDetailsTitle').textContent = 'Robot Utilization Details';

    const s           = data.summary;
    const wHr         = Math.floor(s.working_hours);
    const wMin        = Math.round((s.working_hours - wHr) * 60);
    const wDisplay    = wHr > 0 ? `${wHr}h ${wMin}m` : `${wMin}m`;
    const missionRows = data.mission_rows || [];
    const allTime     = missionRows.length;
    const completed   = missionRows.filter(m => (m.status || '').toUpperCase() === 'COMPLETED').length;
    const cancelled   = missionRows.filter(m => (m.status || '').toUpperCase() === 'CANCELLED').length;
    const allCancelled = allTime > 0 && cancelled === allTime;
    const utilLabel   = allCancelled
        ? '<span style="font-size:11px;background:#f8d7da;color:#721c24;padding:2px 8px;border-radius:8px;">All missions cancelled</span>'
        : 'Most recent work period';

    const hasTelemetry        = (data.battery_rows || []).length > 0 || (data.telemetry_rows || []).length > 0;
    const battHealthDisplay   = hasTelemetry ? `${s.battery_health}%` : 'N/A';
    const battHealthLabel     = hasTelemetry ? 'Avg SOC (estimated)' : 'No telemetry data';
    const chargeCyclesDisplay = hasTelemetry ? (s.charge_cycles_today || 0) : 'N/A';

    const deviceLabel = data.device_name || data.device_id || '';
    document.getElementById('summaryCards').innerHTML = `
        <div class="card"><h3>Utilization Rate</h3><div class="value">${s.utilization_rate}%</div><div class="label">${utilLabel}</div></div>
        <div class="card"><h3>Working Hours</h3><div class="value">${wDisplay}</div><div class="label">Most recent work period</div></div>
        <div class="card"><h3>Total Missions</h3><div class="value">${allTime}</div><div class="label">✅ ${completed} completed &nbsp; ❌ ${cancelled} cancelled</div></div>
        <div class="card"><h3>Battery Health</h3><div class="value">${battHealthDisplay}</div><div class="label">${battHealthLabel}</div></div>
        <div class="card"><h3>Charge Cycles</h3><div class="value">${chargeCyclesDisplay}</div><div class="label">Most recent work period</div></div>
    `;

    // Utilization table
    setHTML('#utilizationTable tbody',
        (data.telemetry_rows || []).length
            ? (data.telemetry_rows || []).map(r => `<tr>
                <td>${r.timestamp ?? ''}</td><td>${r.deviceId ?? ''}</td><td>${r.status ?? ''}</td>
                <td>${r.battery !== null && r.battery !== undefined ? r.battery : ''}</td>
                <td>${fmtSpeed(r.speed)}</td><td>${fmtDriving(r.driving)}</td>
                <td>${r.zoneId ?? ''}</td><td>${r.lastNodeId ?? ''}</td><td>${r.operatingMode ?? ''}</td>
              </tr>`).join('')
            : '<tr><td colspan="9" style="text-align:center;color:#999;">No telemetry data available</td></tr>');

    // Utilization charts
    const u     = data.utilization;
    const workH = u.working_hours || 0;
    const idleH = u.idle_hours > 0 ? u.idle_hours : (workH === 0 ? 24 : 0);
    destroyChart('utilizationChart');
    state.charts.utilizationChart = new Chart(
        document.getElementById('utilizationChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: ['Working Hours', 'Idle Hours'],
            datasets: [{ label: 'Hours', data: [workH, idleH],
                backgroundColor: ['rgba(102,126,234,0.8)', 'rgba(231,76,60,0.8)'] }],
        },
        options: { responsive: true, scales: { y: { beginAtZero: true, title: { display: true, text: 'Hours' } } } },
    });

    const huData    = data.hourly_utilization || [];
    const huAllZero = huData.every(h => h.utilization === 0);
    destroyChart('utilizationTrendChart');
    state.charts.utilizationTrendChart = new Chart(
        document.getElementById('utilizationTrendChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: huData.map(h => h.timestamp),
            datasets: [{
                label: huAllZero ? 'No active missions' : 'Utilization %',
                data: huData.map(h => h.utilization),
                backgroundColor: huAllZero ? 'rgba(200,200,200,0.5)' : 'rgba(102,126,234,0.8)',
                borderColor: huAllZero ? 'rgb(180,180,180)' : 'rgb(102,126,234)',
                borderWidth: 1,
            }],
        },
        options: { responsive: true, scales: {
            x: { title: { display: true, text: 'Time' } },
            y: { beginAtZero: true, max: 100, title: { display: true, text: 'Utilization (%)' },
                 ticks: { callback: v => v + '%' } },
        }},
    });

    // Performance table
    setHTML('#performanceTable tbody',
        (data.mission_rows || []).length
            ? (data.mission_rows || []).map(r => `<tr>
                <td>${r.missionName || ''}</td><td>${r.deviceId || ''}</td>
                <td><span class="status-badge ${getStatusClass(r.status)}">${(r.status || '').toUpperCase()}</span></td>
                <td>${fmtDistance(r.distanceTravelled)}</td>
                <td>${r.completedWaypoints || ''}</td><td>${r.totalWaypoints || ''}</td>
                <td>${r.startedAt || ''}</td><td>${r.completedAt || ''}</td><td>${r.error || ''}</td>
              </tr>`).join('')
            : '<tr><td colspan="9" style="text-align:center;color:#999;">No missions found</td></tr>');

    // Task success chart
    const p = data.task_performance;
    destroyChart('taskSuccessChart');
    state.charts.taskSuccessChart = new Chart(
        document.getElementById('taskSuccessChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: ['Completed', 'Failed', 'Cancelled'],
            datasets: [{ label: 'Tasks', data: [p.completed_tasks, p.failed_tasks, cancelled],
                backgroundColor: ['rgba(46,204,113,0.8)', 'rgba(231,76,60,0.8)', 'rgba(150,150,150,0.8)'] }],
        },
        options: { responsive: true, scales: { y: { beginAtZero: true, title: { display: true, text: 'Tasks' } } } },
    });

    // Task timeline chart
    const tl = data.task_timeline || [];
    const cancelledByHour = {};
    (data.mission_rows || []).filter(m => (m.status || '').toUpperCase() === 'CANCELLED').forEach(m => {
        if (m.startedAt) {
            const d   = new Date(m.startedAt);
            const key = String(d.getDate()).padStart(2, '0') + '-' +
                ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()] +
                ' ' + String(d.getHours()).padStart(2, '0') + ':00';
            cancelledByHour[key] = (cancelledByHour[key] || 0) + 1;
        }
    });
    const cancelledData = tl.map(t => cancelledByHour[t.timestamp] || 0);
    const tlMax = Math.max(...tl.map(t => Math.max(t.completed || 0, t.failed || 0)), ...cancelledData, 0);
    destroyChart('taskTimelineChart');
    state.charts.taskTimelineChart = new Chart(
        document.getElementById('taskTimelineChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: tl.map(t => t.timestamp),
            datasets: [
                { label: 'Completed', data: tl.map(t => t.completed),
                  backgroundColor: 'rgba(46,204,113,0.8)', borderColor: 'rgb(46,204,113)', borderWidth: 1 },
                { label: 'Failed',    data: tl.map(t => t.failed),
                  backgroundColor: 'rgba(231,76,60,0.8)',  borderColor: 'rgb(231,76,60)',  borderWidth: 1 },
                { label: 'Cancelled', data: cancelledData,
                  backgroundColor: 'rgba(150,150,150,0.8)', borderColor: 'rgb(120,120,120)', borderWidth: 1 },
            ],
        },
        options: { responsive: true, scales: {
            x: { title: { display: true, text: 'Time' } },
            y: { beginAtZero: true, max: tlMax > 0 ? Math.ceil(tlMax * 1.3) : 5,
                 title: { display: true, text: 'Tasks' },
                 ticks: { stepSize: 1, callback: v => Number.isInteger(v) ? v : '' } },
        }},
        plugins: [{ afterDatasetsDraw(chart) {
            const ctx    = chart.ctx;
            const colors = ['#27ae60', '#c0392b', '#7f8c8d'];
            chart.data.datasets.forEach((ds, di) => {
                chart.getDatasetMeta(di).data.forEach((bar, i) => {
                    const v = ds.data[i];
                    if (v > 0) {
                        ctx.fillStyle    = colors[di] || '#333';
                        ctx.font         = 'bold 11px Arial';
                        ctx.textAlign    = 'center';
                        ctx.textBaseline = 'bottom';
                        ctx.fillText(v, bar.x, bar.y - 3);
                    }
                });
            });
        }}],
    });

    // Battery drain chart
    const drainData = data.hourly_drain || data.hourly_energy || [];
    const drainVals = drainData.map(d => d.drain_pct !== undefined ? d.drain_pct : (d.energy_kwh || 0));
    const drainMax  = Math.max(...drainVals, 0);
    destroyChart('batteryDrainChart');
    state.charts.batteryDrainChart = new Chart(
        document.getElementById('batteryDrainChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: drainData.map(d => d.timestamp),
            datasets: [{
                label: 'Battery Drain (% / hour)',
                data:  drainVals,
                backgroundColor: 'rgba(241,196,15,0.85)',
                borderColor: 'rgb(241,196,15)', borderWidth: 1,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: true,
            plugins: { legend: { display: true, position: 'top' },
                tooltip: { callbacks: { label: ctx => `Drained: ${ctx.parsed.y.toFixed(1)}% this hour` } } },
            scales: {
                x: { title: { display: true, text: 'Time' } },
                y: { beginAtZero: true,
                     max: drainMax > 0 ? Math.min(100, Math.ceil(drainMax * 1.2)) : 10,
                     title: { display: true, text: 'Battery Drain (%)' },
                     ticks: { callback: v => v + '%' } },
            },
        },
    });

    // Battery level chart
    const levelData = data.hourly_level || [];
    const levelVals = levelData.map(d => d.avg_level);
    destroyChart('batteryLevelChart');
    state.charts.batteryLevelChart = new Chart(
        document.getElementById('batteryLevelChart').getContext('2d'), {
        type: 'line',
        data: {
            labels: levelData.map(d => d.timestamp),
            datasets: [{
                label: 'Battery Level (%)',
                data:  levelVals,
                borderColor: 'rgba(52,152,219,1)',
                backgroundColor: 'rgba(52,152,219,0.1)',
                borderWidth: 2, pointRadius: 3,
                pointBackgroundColor: 'rgba(52,152,219,1)',
                fill: true, tension: 0.3, spanGaps: true,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: true,
            plugins: { legend: { display: true, position: 'top' },
                tooltip: { callbacks: {
                    label: ctx => ctx.parsed.y !== null ? `Battery: ${ctx.parsed.y.toFixed(1)}%` : 'No data',
                } } },
            scales: {
                x: { title: { display: true, text: 'Time' } },
                y: { beginAtZero: false, min: 0, max: 100,
                     title: { display: true, text: 'Battery Level (%)' },
                     ticks: { callback: v => v + '%' } },
            },
        },
    });

    // Battery table
    const bRows = data.battery_rows || [];
    setHTML('#batteryTable tbody', bRows.length
        ? bRows.map(r => {
            const bc = r.battery > 60
                ? 'rgba(46,204,113,0.8)' : r.battery > 30
                ? 'rgba(241,196,15,0.8)' : 'rgba(231,76,60,0.8)';
            return `<tr>
                <td>${r.timestamp || ''}</td><td>${r.deviceId || ''}</td>
                <td><div class="progress-bar"><div class="progress-fill" style="width:${r.battery || 0}%;background:${bc}"></div></div>${r.battery || ''}%</td>
                <td>${r.status || ''}</td><td>${r.speed || ''}</td>
                <td>${r.zoneId || ''}</td><td>${r.lastNodeId || ''}</td>
                <td><span class="charge-cycles-badge">⚡ ${r.charge_cycles_today || 0}</span></td>
            </tr>`;
        }).join('')
        : '<tr><td colspan="8" style="text-align:center;color:#999;">No battery data available</td></tr>');
}

// ── Mission Render ────────────────────────────────────────────────────────────
function renderMission(mData) {
    const mission     = mData.mission;
    const battery     = mData.battery || {};
    const util        = mData.utilization || {};
    const telemetry   = mData.telemetry_rows || [];
    const missionName = mission.missionName || 'Mission';

    document.getElementById('viewModeBadge').textContent = '🎯 ' + missionName.substring(0, 30);
    document.getElementById('utilizationChartsGrid').style.display = 'none';
    document.getElementById('performanceChartsGrid').style.display = 'none';
    document.getElementById('batteryChartsGrid').style.display = 'none';
    document.getElementById('utilizationDetailsTitle').textContent = 'Mission Utilization Details';

    const durationSec = util.mission_duration_seconds || 0;
    const duration    = util.mission_duration_formatted || 'N/A';
    const durationH   = (durationSec / 3600).toFixed(4);
    const utilPct     = util.utilization_percent !== undefined
        ? util.utilization_percent.toFixed(2)
        : ((durationSec / 86400) * 100).toFixed(2);
    const isCancelled = ['CANCELLED', 'FAILED'].includes((mission.status || '').toUpperCase());
    const dist        = fmtDistance(mission.distanceTravelled);

    // Summary cards
    const robotName = mData.device_name || mData.device_id || '';
    document.getElementById('summaryCards').innerHTML = `
        <div class="card"><h3>Mission Name</h3><div class="value" style="font-size:20px;">${missionName}</div><div class="label">Selected Mission</div></div>
        <div class="card"><h3>Status</h3><div class="value"><span class="status-badge ${getStatusClass(mission.status)}">${(mission.status || 'N/A').toUpperCase()}</span></div><div class="label">Mission Status</div></div>
        <div class="card"><h3>Distance</h3><div class="value">${dist}m</div><div class="label">Traveled</div></div>
        <div class="card"><h3>Waypoints</h3><div class="value">${mission.completedWaypoints || 0}/${mission.totalWaypoints || 0}</div><div class="label">Completed</div></div>
        <div class="card"><h3>Duration</h3><div class="value">${duration}</div>
            <div class="label">${durationSec >= 86400
                ? '<span style="color:#e74c3c;font-size:11px;">⚠️ Data anomaly</span>'
                : 'Mission Time'}</div>
        </div>
    `;

    // Mission info card — now includes endpoint ID and operating mode
    const lastRow       = telemetry.length ? telemetry[telemetry.length - 1] : null;
    const endpointLabel = lastRow ? (lastRow.endpointId || mission.deviceId) : mission.deviceId;
    const lastMode      = lastRow ? (lastRow.operatingMode || '—') : '—';
    const lastZone      = lastRow ? (lastRow.zoneId || '—') : '—';

    document.getElementById('missionInfoCard').style.display = 'block';
    document.getElementById('missionInfoCard').innerHTML = `
        <h2 style="margin:0 0 20px 0;">📋 Mission Details</h2>
        <div class="mission-info-grid">
            <div class="mission-info-item"><label>Started At</label><div class="value">${fmtTimestamp(mission.startedAt)}</div></div>
            <div class="mission-info-item"><label>Completed At</label><div class="value">${mission.completedAt ? fmtTimestamp(mission.completedAt) : 'Running...'}</div></div>
            <div class="mission-info-item"><label>Robot</label><div class="value">${robotName || mission.deviceId || 'N/A'}</div></div>
            <div class="mission-info-item"><label>Last Zone</label><div class="value">${lastZone}</div></div>
            <div class="mission-info-item"><label>Final Mode</label><div class="value">${lastMode}</div></div>
            <div class="mission-info-item"><label>Error</label><div class="value">${mission.error || 'None'}</div></div>
        </div>
    `;

    // Utilization detail
    document.getElementById('utilizationContent').style.display = 'block';
    document.getElementById('utilizationTable').style.display = 'none';
    document.getElementById('utilizationContent').innerHTML = `
        <div class="metric-row">
            <div class="metric-label">📊 Physical Utilization</div>
            <div class="metric-value">${utilPct}%</div>
            <div class="metric-desc">Robot was physically active for ${utilPct}% of the 24-hour period</div>
        </div>
        <div class="metric-row">
            <div class="metric-label">⏱️ Active Duration</div>
            <div class="metric-value">${duration}</div>
            <div class="metric-desc">${durationSec}s of physical robot activity</div>
        </div>
        <div class="metric-row">
            <div class="metric-label">💤 Idle Time</div>
            <div class="metric-value">${(24 - parseFloat(durationH)).toFixed(4)}h</div>
            <div class="metric-desc">${(100 - parseFloat(utilPct)).toFixed(2)}% of 24h not in this mission</div>
        </div>
        ${isCancelled ? `<div style="margin-top:12px;padding:12px;background:#fff9e6;border-left:4px solid #f39c12;border-radius:6px;">
            <strong style="color:#d68910;">⚠️ Cancelled Mission:</strong>
            <span style="font-size:12px;color:#666;"> This mission was cancelled after ${duration}.
            Excluded from device overview utilization since the goal was not completed.</span>
        </div>` : ''}
    `;

    // ── NEW: Rich Mission Intelligence Panel (replaces duplicate performance table) ──
    renderMissionIntelligence(telemetry, mission, battery, durationSec, dist);

    // Battery detail
    const batteryUsed  = parseFloat(battery.battery_used) || 0;
    const startBatt    = battery.start_battery;
    const endBatt      = battery.end_battery;
    const avgBatt      = battery.avg_battery;
    const method       = battery.measurement_method || 'unavailable';
    const hasData      = method !== 'unavailable';
    const hasRealDrain = hasData && batteryUsed >= 0.01;

    const methodBadge = {
        'direct':       '<span style="background:#d4edda;color:#155724;padding:4px 12px;border-radius:6px;font-size:12px;font-weight:600;">✅ Directly measured</span>',
        'interpolated': '<span style="background:#cce5ff;color:#004085;padding:4px 12px;border-radius:6px;font-size:12px;font-weight:600;">📐 Time-proportional estimate</span>',
        'unavailable':  '<span style="background:#f8d7da;color:#721c24;padding:4px 12px;border-radius:6px;font-size:12px;font-weight:600;">❌ No telemetry data</span>',
    }[method] || '';

    const usedDisplay = !hasData     ? '—'
                      : hasRealDrain ? `${batteryUsed.toFixed(2)}%`
                      :               '< 0.01%';
    const usedDesc    = !hasData     ? 'No telemetry data available'
                      : hasRealDrain ? `${batteryUsed.toFixed(2)}% attributed to this mission (time-proportional share)`
                      :               'Below telemetry resolution — mission too short relative to poll interval';
    const drainRate   = !hasData     ? '—'
                      : hasRealDrain ? `${(batteryUsed / durationSec).toFixed(5)}%/s`
                      :               '< 0.001%/s';
    const speedKmh    = ((parseFloat(mission.distanceTravelled) || 0) / (durationSec || 1) * 3.6).toFixed(2);

    const accountingNote = hasData ? `
        <div class="battery-accounting-note">
            <strong>ℹ️ Battery accounting:</strong>
            Each mission shows its time-proportional share of battery drain.
            The hourly drain chart includes both mission drain and standby drain (robot idle between missions).
            Summing all mission values gives only the mission portion — the rest is standby.
        </div>` : '';

    document.getElementById('batteryContent').style.display = 'block';
    document.getElementById('batteryTable').style.display = 'none';
    document.getElementById('batteryContent').innerHTML = `
        <div style="margin-bottom:16px;">${methodBadge}</div>
        <div class="two-column-grid">
            <div>
                <h3 class="column-header" style="color:#667eea;border-color:#667eea;">🔋 Battery Metrics</h3>
                <div class="metric-row"><div class="metric-label">🔋 Start</div><div class="metric-value">${hasData && startBatt !== null ? startBatt + '%' : '—'}</div><div class="metric-desc">Level at mission start</div></div>
                <div class="metric-row"><div class="metric-label">🔋 End</div><div class="metric-value">${hasData && endBatt !== null ? endBatt + '%' : '—'}</div><div class="metric-desc">Level at mission end</div></div>
                <div class="metric-row"><div class="metric-label">⚡ Used</div>
                    <div class="metric-value" style="color:${hasRealDrain ? '#e74c3c' : '#999'};">${usedDisplay}</div>
                    <div class="metric-desc">${usedDesc}</div></div>
                <div class="metric-row"><div class="metric-label">📊 Average</div><div class="metric-value">${hasData && avgBatt !== null ? avgBatt + '%' : '—'}</div><div class="metric-desc">Average level during mission</div></div>
                ${accountingNote}
            </div>
            <div>
                <h3 class="column-header" style="color:#ffa94d;border-color:#ffa94d;">⚡ Energy Metrics</h3>
                <div class="metric-row"><div class="metric-label">⏱️ Duration</div><div class="metric-value">${duration}</div><div class="metric-desc">Total execution time</div></div>
                <div class="metric-row"><div class="metric-label">📏 Distance</div><div class="metric-value">${dist}m</div><div class="metric-desc">Total distance traveled</div></div>
                <div class="metric-row"><div class="metric-label">🚀 Avg Speed</div><div class="metric-value">${speedKmh} km/h</div><div class="metric-desc">Average velocity</div></div>
                <div class="metric-row"><div class="metric-label">⚡ Drain Rate</div><div class="metric-value">${drainRate}</div><div class="metric-desc">${hasRealDrain ? 'Measured drain per second' : 'Below detection threshold'}</div></div>
            </div>
        </div>
    `;
}

// ── Mission Intelligence Panel ────────────────────────────────────────────────
function renderMissionIntelligence(telemetry, mission, battery, durationSec, dist) {
    // Use the dedicated mission intelligence container div (more reliable than .closest())
    let container = document.getElementById('missionIntelligencePanel');
    if (!container) {
        // Fallback: try to find via performanceTable parent
        const perfTable = document.querySelector('#performanceTable');
        container = perfTable ? perfTable.closest('.card') : null;
    }
    if (!container) {
        console.warn('[renderMissionIntelligence] Container not found — skipping intelligence panel');
        return;
    }

    // Extract usable data
    const pathPoints   = telemetry.filter(r => r.pos_x !== '' && r.pos_x !== null && r.pos_x !== undefined
                                            && r.pos_y !== '' && r.pos_y !== null && r.pos_y !== undefined);
    const speedPoints  = telemetry.filter(r => r.speed !== '' && r.speed !== null && r.speed !== undefined);
    const errorEvents  = telemetry.filter(r => Array.isArray(r.errors) && r.errors.length > 0);
    const modeChanges  = [];
    let   lastMode     = null;
    telemetry.forEach(r => {
        const m = r.operatingMode || '';
        if (m && m !== lastMode) {
            modeChanges.push({ timestamp: r.timestamp, mode: m });
            lastMode = m;
        }
    });

    const hasPath   = pathPoints.length >= 2;
    const hasSpeed  = speedPoints.length >= 2;
    const hasErrors = errorEvents.length > 0;
    const hasModes  = modeChanges.length > 0;
    const noData    = !hasPath && !hasSpeed && !hasErrors && !hasModes;

    container.innerHTML = `
        <h2>🧠 Mission Intelligence</h2>
        <p style="color:#888;font-size:13px;margin:-8px 0 20px;">
            Live telemetry analysis for this mission window
        </p>
        ${noData ? '<div style="text-align:center;color:#999;padding:40px 0;">No telemetry recorded during this mission window</div>' : `
        <div class="intel-grid">

            ${hasPath ? `
            <div class="intel-panel">
                <h3 class="intel-title">🗺️ Robot Path</h3>
                <p class="intel-subtitle">${pathPoints.length} position fixes — X/Y coordinates (metres)</p>
                <canvas id="pathCanvas" style="width:100%;border-radius:8px;background:#f8f9fa;"></canvas>
                <div style="display:flex;gap:16px;margin-top:8px;font-size:12px;color:#666;">
                    <span>🟢 Start</span><span>🔴 End</span><span>→ Direction (theta)</span>
                </div>
            </div>` : ''}

            ${hasSpeed ? `
            <div class="intel-panel">
                <h3 class="intel-title">🚀 Speed Profile</h3>
                <p class="intel-subtitle">Robot velocity over mission duration (m/s)</p>
                <canvas id="speedChart"></canvas>
            </div>` : ''}

            ${hasErrors ? `
            <div class="intel-panel">
                <h3 class="intel-title">⚠️ Error Events</h3>
                <p class="intel-subtitle">${errorEvents.length} telemetry snapshot(s) with active error codes</p>
                <div class="error-event-list">
                    ${errorEvents.map(r => `
                        <div class="error-event-row">
                            <span class="error-time">${fmtTimestamp(r.timestamp)}</span>
                            <span class="error-codes">
                                ${r.errors.map(e => `<span class="error-badge">${e}</span>`).join(' ')}
                            </span>
                        </div>
                    `).join('')}
                </div>
            </div>` : `
            <div class="intel-panel">
                <h3 class="intel-title">✅ Error Events</h3>
                <p class="intel-subtitle">No errors recorded during this mission</p>
                <div style="text-align:center;padding:32px 0;color:#27ae60;font-size:32px;">✅</div>
                <div style="text-align:center;color:#27ae60;font-size:13px;font-weight:600;">Clean run — zero error codes</div>
            </div>`}

            ${hasModes ? `
            <div class="intel-panel">
                <h3 class="intel-title">🔄 Operating Mode Timeline</h3>
                <p class="intel-subtitle">${modeChanges.length} mode transition(s) detected</p>
                <div class="mode-timeline">
                    ${modeChanges.map((mc, i) => `
                        <div class="mode-event">
                            <div class="mode-dot ${getModeClass(mc.mode)}"></div>
                            <div class="mode-info">
                                <div class="mode-name">${mc.mode}</div>
                                <div class="mode-time">${fmtTimestamp(mc.timestamp)}</div>
                            </div>
                            ${i < modeChanges.length - 1 ? '<div class="mode-line"></div>' : ''}
                        </div>
                    `).join('')}
                </div>
            </div>` : ''}

        </div>`}
    `;

    // Draw path on canvas
    if (hasPath) {
        requestAnimationFrame(() => drawPath(pathPoints));
    }

    // Draw speed chart
    if (hasSpeed) {
        destroyChart('speedChart');
        const labels = speedPoints.map(r => {
            try { return new Date(r.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
            catch (_) { return r.timestamp; }
        });
        const speeds = speedPoints.map(r => parseFloat(r.speed) || 0);
        const maxSpd = Math.max(...speeds, 0);
        state.charts.speedChart = new Chart(
            document.getElementById('speedChart').getContext('2d'), {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: 'Speed (m/s)',
                    data: speeds,
                    borderColor: 'rgba(155,89,182,1)',
                    backgroundColor: 'rgba(155,89,182,0.15)',
                    borderWidth: 2,
                    pointRadius: speeds.length > 60 ? 0 : 3,
                    pointBackgroundColor: 'rgba(155,89,182,1)',
                    fill: true,
                    tension: 0.3,
                    spanGaps: false,
                }],
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: ctx => `${ctx.parsed.y.toFixed(3)} m/s` } },
                },
                scales: {
                    x: { title: { display: true, text: 'Time' },
                         ticks: { maxTicksLimit: 8, maxRotation: 45 } },
                    y: { beginAtZero: true,
                         max: maxSpd > 0 ? Math.ceil(maxSpd * 1.2 * 10) / 10 : 2,
                         title: { display: true, text: 'Speed (m/s)' } },
                },
            },
        });
    }
}

// ── Path Visualizer (Canvas 2D) ───────────────────────────────────────────────
function drawPath(points) {
    const canvas = document.getElementById('pathCanvas');
    if (!canvas) return;

    const PAD  = 40;
    const W    = canvas.parentElement.clientWidth || 400;
    const H    = Math.max(260, W * 0.55);
    canvas.width  = W;
    canvas.height = H;
    const ctx  = canvas.getContext('2d');

    const xs = points.map(p => parseFloat(p.pos_x));
    const ys = points.map(p => parseFloat(p.pos_y));
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const rangeX = maxX - minX || 1;
    const rangeY = maxY - minY || 1;

    const scaleX = (W - PAD * 2) / rangeX;
    const scaleY = (H - PAD * 2) / rangeY;
    const scale  = Math.min(scaleX, scaleY);

    const offX = PAD + ((W - PAD * 2) - rangeX * scale) / 2;
    const offY = PAD + ((H - PAD * 2) - rangeY * scale) / 2;

    const toCanvas = (x, y) => ({
        cx: offX + (x - minX) * scale,
        cy: H - (offY + (y - minY) * scale),   // flip Y (SVG coords vs cartesian)
    });

    // Background grid
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = '#e8eaed';
    ctx.lineWidth   = 1;
    for (let gx = 0; gx <= 6; gx++) {
        const x = PAD + (gx / 6) * (W - PAD * 2);
        ctx.beginPath(); ctx.moveTo(x, PAD); ctx.lineTo(x, H - PAD); ctx.stroke();
    }
    for (let gy = 0; gy <= 4; gy++) {
        const y = PAD + (gy / 4) * (H - PAD * 2);
        ctx.beginPath(); ctx.moveTo(PAD, y); ctx.lineTo(W - PAD, y); ctx.stroke();
    }

    // Path trail — gradient from blue (start) to red (end)
    for (let i = 1; i < points.length; i++) {
        const a  = toCanvas(xs[i - 1], ys[i - 1]);
        const b  = toCanvas(xs[i],     ys[i]);
        const t  = i / points.length;
        const r  = Math.round(52  + (231 - 52)  * t);
        const g  = Math.round(152 + (76  - 152) * t);
        const bl = Math.round(219 + (60  - 219) * t);
        ctx.strokeStyle = `rgb(${r},${g},${bl})`;
        ctx.lineWidth   = 2.5;
        ctx.lineCap     = 'round';
        ctx.beginPath();
        ctx.moveTo(a.cx, a.cy);
        ctx.lineTo(b.cx, b.cy);
        ctx.stroke();
    }

    // Direction arrows every N points
    const arrowEvery = Math.max(1, Math.floor(points.length / 8));
    ctx.fillStyle = 'rgba(100,100,200,0.7)';
    for (let i = arrowEvery; i < points.length; i += arrowEvery) {
        const prev = toCanvas(xs[i - 1], ys[i - 1]);
        const curr = toCanvas(xs[i],     ys[i]);
        const ang  = Math.atan2(curr.cy - prev.cy, curr.cx - prev.cx);
        ctx.save();
        ctx.translate(curr.cx, curr.cy);
        ctx.rotate(ang);
        ctx.beginPath();
        ctx.moveTo(6,  0);
        ctx.lineTo(-4, -3);
        ctx.lineTo(-4,  3);
        ctx.closePath();
        ctx.fill();
        ctx.restore();
    }

    // Start point (green)
    const start = toCanvas(xs[0], ys[0]);
    ctx.beginPath();
    ctx.arc(start.cx, start.cy, 7, 0, Math.PI * 2);
    ctx.fillStyle = '#27ae60';
    ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();

    // End point (red)
    const end = toCanvas(xs[xs.length - 1], ys[ys.length - 1]);
    ctx.beginPath();
    ctx.arc(end.cx, end.cy, 7, 0, Math.PI * 2);
    ctx.fillStyle = '#e74c3c';
    ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();

    // Axis labels
    ctx.fillStyle    = '#999';
    ctx.font         = '11px Arial';
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText(`${minX.toFixed(1)}m`, PAD, H - PAD + 6);
    ctx.fillText(`${maxX.toFixed(1)}m`, W - PAD, H - PAD + 6);
    ctx.textAlign    = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText(`${minY.toFixed(1)}m`, PAD - 6, H - PAD);
    ctx.fillText(`${maxY.toFixed(1)}m`, PAD - 6, PAD);
}

function getModeClass(mode) {
    if (!mode) return 'mode-dot-idle';
    const m = mode.toUpperCase();
    if (m === 'AUTOMATIC') return 'mode-dot-auto';
    if (m === 'MANUAL')    return 'mode-dot-manual';
    return 'mode-dot-idle';
}

// ── Entry Point ───────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);