let f_data = null;
let f_rates = null;
let f_symbol = '';
let f_mode = 'quarterly'; // 'quarterly' or 'yearly'
let f_charts = {};

window.openFinancialModal = function() {
    const sec = document.getElementById('fundamentalSection');
    if (sec.style.display === 'none' || sec.style.display === '') {
        sec.style.display = 'flex';
        setTimeout(() => {
            sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 100);
        
        let sym = 'FPT';
        const displayEl = document.getElementById('currentSymbolDisplay');
        if (displayEl) {
            sym = displayEl.innerText.trim().toUpperCase() || 'FPT';
        }
        
        if (sym !== f_symbol) {
            loadFundamental(sym);
        }
    } else {
        sec.style.display = 'none';
    }
};

const originalFetchStockData = window.fetchStockData;
if (originalFetchStockData) {
    window.fetchStockData = async function(symbol, chartIdx = 1) {
        await originalFetchStockData(symbol, chartIdx);
        const sec = document.getElementById('fundamentalSection');
        if (sec && sec.style.display !== 'none' && chartIdx === 1) {
            loadFundamental(symbol);
        }
    };
}

async function loadFundamental(symbol) {
    f_symbol = symbol.toUpperCase();
    const sec = document.getElementById('fundamentalSection');
    
    const isFirstLoad = !sec.querySelector('.fd-header');
    
    if (isFirstLoad) {
        sec.innerHTML = `
            <div class="fd-header">
                <div class="fd-title">📊 Đánh giá cơ bản: ${f_symbol} <div class="fd-spinner-small"></div></div>
                <button class="fd-close-btn" onclick="window.openFinancialModal()">✕</button>
            </div>
            <div class="fd-loading-container" style="padding: 100px; display: flex; justify-content: center;">
                <div class="fd-spinner"></div>
            </div>
        `;
    } else {
        const titleEl = sec.querySelector('.fd-title');
        if (titleEl) {
            titleEl.innerHTML = `📊 Đánh giá cơ bản: ${f_symbol} <div class="fd-spinner-small"></div>`;
        }
        ['.fd-verdict-container', '.fd-charts-grid', '.fd-table-container'].forEach(cls => {
            const el = sec.querySelector(cls);
            if (el) el.style.opacity = '0.4';
        });
    }
    
    try {
        const [resFund, resRate] = await Promise.all([
            fetch(`/api/fundamental?symbol=${f_symbol}`),
            fetch(`/api/interest-rates`)
        ]);
        const data = await resFund.json();
        const rates = await resRate.json();
        
        if (data.error) throw new Error(data.error);
        
        f_data = data;
        f_rates = rates;
        renderFundamental();
    } catch (e) {
        sec.innerHTML = `
            <div class="fd-header">
                <div class="fd-title">📊 Đánh giá cơ bản: ${f_symbol}</div>
                <button class="fd-close-btn" onclick="window.openFinancialModal()">✕</button>
            </div>
            <div style="padding: 40px; text-align: center; color: var(--fd-negative);">Lỗi tải dữ liệu: ${e.message}</div>
        `;
    }
}

function setFundamentalMode(mode) {
    f_mode = mode;
    renderFundamental();
}

function getAlignedData(overrideMode = null) {
    const mode = overrideMode || f_mode;
    let periods = mode === 'quarterly' ? f_data.quarters : f_data.years;
    let stats = f_data.stats || [];
    
    periods = periods.slice().sort((a, b) => {
        if (mode === 'quarterly') {
            if (a.yearReport !== b.yearReport) return a.yearReport - b.yearReport;
            return a.lengthReport - b.lengthReport;
        } else {
            return a.yearReport - b.yearReport;
        }
    });
    
    let result = [];
    periods.forEach((p, idx) => {
        const yr = p.yearReport;
        const qt = mode === 'quarterly' ? p.lengthReport : 0;
        
        let label = mode === 'quarterly' ? `Q${qt}/${yr}` : `${yr}`;
        
        let stat = stats.find(s => {
            if (mode === 'quarterly') return parseInt(s.year) === yr && s.quarter === qt;
            return parseInt(s.year) === yr && (s.quarter === 0 || s.quarter === 4);
        });
        
        if (!stat && mode === 'yearly') {
            stat = stats.find(s => parseInt(s.year) === yr && s.quarter === 4);
        }
        if (!stat) {
            let yearStats = stats.filter(s => parseInt(s.year) === yr);
            if (yearStats.length > 0) {
                yearStats.sort((a,b) => b.quarter - a.quarter);
                stat = yearStats[0];
            }
        }
        
        stat = stat || {};
        
        let eps = 0;
        if (stat.marketCap && stat.pe && stat.numberOfSharesMktCap) {
            eps = (stat.marketCap / stat.pe) / stat.numberOfSharesMktCap;
        } else if (p.isa22 && stat.numberOfSharesMktCap) {
            eps = p.isa22 / stat.numberOfSharesMktCap;
        }
        
        let prevP = null;
        if (mode === 'quarterly') {
            prevP = result.find(r => r.year === yr - 1 && r.quarter === qt);
        } else {
            if (idx > 0) prevP = result[idx - 1];
        }
        
        let rev = p.isa3;
        if (!rev || rev === 0) rev = p.isb38; // Banks
        if (!rev || rev === 0) rev = p.iss115; // Securities fallback
        
        let prof = p.isa22;
        if (!prof || prof === 0) prof = p.isb40; // Banks fallback
        
        let profitYoY = 0;
        let revYoY = 0;
        let epsYoY = 0;
        
        if (prevP) {
            if (prevP.profit !== 0) profitYoY = (prof - prevP.profit) / Math.abs(prevP.profit);
            if (prevP.revenue !== 0) revYoY = (rev - prevP.revenue) / Math.abs(prevP.revenue);
            if (prevP.eps !== 0) epsYoY = (eps - prevP.eps) / Math.abs(prevP.eps);
        }
        
        result.push({
            label,
            year: yr,
            quarter: qt,
            revenue: rev || 0,
            profit: prof || 0,
            profitYoY: profitYoY,
            revYoY: revYoY,
            eps: eps,
            epsYoY: epsYoY,
            pe: stat.pe || 0,
            pb: stat.pb || 0,
            roe: stat.roe || 0,
            roa: stat.roa || 0
        });
    });
    
    return result;
}

function processInterestRates() {
    let vcb = 5.0, tcb = 5.0, maxRate = 5.0, avgRate = 5.0;
    if (f_rates && f_rates.length > 0) {
        let latestDate = Math.max(...f_rates.map(r => parseInt(r.DateConvert)));
        let latestRates = f_rates.filter(r => parseInt(r.DateConvert) === latestDate);
        
        let sum = 0;
        let count = 0;
        maxRate = 0;
        latestRates.forEach(r => {
            let rate = parseFloat(r.InterestRate);
            if (!isNaN(rate) && r.BankName !== "TRUNG BÌNH") {
                sum += rate;
                count++;
                if (rate > maxRate) maxRate = rate;
                if (r.BankName && r.BankName.toUpperCase().includes('VIETCOMBANK')) vcb = rate;
                if (r.BankName && r.BankName.toUpperCase().includes('TECHCOMBANK')) tcb = rate;
            }
        });
        if (count > 0) avgRate = sum / count;
    }
    return { vcb, tcb, maxRate, avgRate };
}

function calculateScore(data, ratesInfo) {
    if (!data || data.length === 0) return { score: 0, verdict: 'Không đủ dữ liệu', color: '#888' };
    
    // Luôn luôn nhận vào quarterlyData nên không cần filter theo f_mode nữa
    const latest = data[data.length - 1];
    
    // Tiêu chí 1 — Tăng trưởng LN (20đ)
    // Lấy 5 kỳ gần nhất (luôn là 5 Quý gần nhất)
    const recent = data.slice(-5);
    let scoreProfit = 0;
    if (recent.length >= 2) {
        const totalChange = recent[recent.length - 1].profit - recent[0].profit;
        const allPositive = recent.every(y => y.profit > 0);
        const latestIsMax = recent[recent.length - 1].profit === Math.max(...recent.map(y => y.profit));
        
        if (totalChange > 0 && latestIsMax) scoreProfit = 20;
        else if (totalChange > 0 && allPositive) scoreProfit = 15;
        else if (totalChange > 0) scoreProfit = 10;
        else scoreProfit = 0;
    }
    
    // Tiêu chí 2 — ROE (20đ)
    let scoreROE = 0;
    let roe = latest.roe * 100; // roe đang ở dạng thập phân 0.15 => 15%
    if (roe >= 20) scoreROE = 20;
    else if (roe >= 17) scoreROE = 15;
    else if (roe >= 15) scoreROE = 10;
    
    // Tiêu chí 3 — P/E (20đ)
    let scorePE = 0;
    let pe = latest.pe;
    if (pe >= 2 && pe <= 5) scorePE = 20;
    else if (pe > 5 && pe <= 7) scorePE = 15;
    else if (pe > 7 && pe <= 10) scorePE = 10;
    
    // Tiêu chí 4 — EPS (20đ)
    let scoreEPS = 0;
    let eps = latest.eps;
    if (eps >= 4000) scoreEPS = 20;
    else if (eps >= 3000) scoreEPS = 15;
    else if (eps >= 2000) scoreEPS = 10;
    
    // Tiêu chí 5 — E/P vs Lãi suất cao nhất hiện tại (20đ)
    let scoreEP = 0;
    let ep = pe > 0 ? (1 / pe) * 100 : 0;
    let spread = ep - ratesInfo.maxRate;
    if (spread > 4) scoreEP = 20;
    else if (spread > 2) scoreEP = 15;
    else if (spread >= 0) scoreEP = 10;
    
    let totalScore = scoreProfit + scoreROE + scorePE + scoreEPS + scoreEP;
    
    // Veto rule
    let veto = (scoreProfit === 0 || scoreROE === 0 || scorePE === 0 || scoreEPS === 0 || scoreEP === 0);
    
    let verdict = '';
    let color = '';
    if (veto || totalScore < 40) {
        verdict = 'TRUNG LẬP'; color = '#EAB308';
    } else if (totalScore >= 80) {
        verdict = 'KHẢ QUAN'; color = '#15803D';
    } else if (totalScore >= 60) {
        verdict = 'KHẢ QUAN'; color = '#2563EB';
    } else {
        verdict = 'THEO DÕI'; color = '#D97706';
    }
    
    let stockType = '';
    let typeColor = '';
    let coreVeto = (scoreProfit === 0 || scoreROE === 0 || scoreEPS === 0);
    let valuationVeto = (scorePE === 0 || scoreEP === 0);

    if (totalScore < 50 || coreVeto) {
        stockType = 'CỔ PHIẾU ĐẦU CƠ';
        typeColor = '#EC4899'; // Pink
    } else if (valuationVeto) {
        stockType = 'CƠ BẢN TỐT NHƯNG ĐỊNH GIÁ CAO';
        typeColor = '#0EA5E9'; // Sky blue
    } else {
        stockType = 'CƠ BẢN TỐT & HẤP DẪN';
        typeColor = '#A855F7'; // Violet
    }
    
    return { score: totalScore, verdict, color, type: stockType, typeColor, breakdown: { scoreProfit, scoreROE, scorePE, scoreEPS, scoreEP } };
}

// Insight Engine Rule-based (Redesigned)
function getInsightYoY(data) {
    if (data.length < 2) return '';
    const latest = data[data.length - 1];
    if (latest.profitYoY > 0) return `↑ LNST đạt ${formatB(latest.profit)}, tăng ${(latest.profitYoY * 100).toFixed(1)}% YoY. Đà tăng trưởng vững chắc.`;
    return `↓ LNST đạt ${formatB(latest.profit)}, giảm ${(Math.abs(latest.profitYoY) * 100).toFixed(1)}% YoY.`;
}

function getInsightScale(data) {
    if (data.length < 2) return '';
    const latest = data[data.length - 1];
    const maxRev = Math.max(...data.map(d => d.revenue));
    if (latest.revenue >= maxRev) return `↑ Doanh thu kỷ lục ${formatB(latest.revenue)}. Quy mô hoạt động mở rộng tích cực.`;
    return `→ Doanh thu duy trì ${formatB(latest.revenue)}, chưa phá đỉnh ${formatB(maxRev)}.`;
}

function getInsightROE(data) {
    if (data.length < 2) return '';
    const latest = data[data.length - 1];
    const avg = data.reduce((a,b) => a + b.roe, 0) / data.length;
    if (latest.roe > avg) return `↑ ROE ${(latest.roe * 100).toFixed(1)}% > TB dài hạn. Hiệu quả sử dụng vốn đang cải thiện.`;
    return `↓ ROE ${(latest.roe * 100).toFixed(1)}% < TB dài hạn. Cần theo dõi hiệu quả kinh doanh.`;
}

function getInsightValuation(data) {
    if (data.length < 2) return '';
    const latest = data[data.length - 1];
    const avgPE = data.reduce((a,b) => a + b.pe, 0) / data.length;
    if (latest.pe < avgPE) return `↑ P/E ${latest.pe.toFixed(1)}x rẻ hơn TB lịch sử. Tạo ra biên an toàn tốt.`;
    return `↓ P/E ${latest.pe.toFixed(1)}x đắt hơn trung bình lịch sử.`;
}

function getInsightEPS(data) {
    if (data.length < 2) return '';
    const latest = data[data.length - 1];
    if (latest.epsYoY > 0) return `↑ EPS ${latest.eps.toFixed(0)} đ/cp. Tăng trưởng dương, tạo cơ sở vững chắc.`;
    return `↓ EPS ${latest.eps.toFixed(0)} đ/cp. Tăng trưởng âm, gây áp lực định giá.`;
}

function getInsightEP(data, ratesInfo) {
    if (data.length < 1) return '';
    const latest = data[data.length - 1];
    const ep = latest.pe > 0 ? (1 / latest.pe) * 100 : 0;
    if (ep > ratesInfo.maxRate) return `↑ E/P ${ep.toFixed(1)}% vượt trội lãi suất thị trường (${ratesInfo.maxRate.toFixed(1)}%).`;
    if (ep > ratesInfo.avgRate) return `→ E/P tốt hơn lãi suất trung bình (${ratesInfo.avgRate.toFixed(1)}%).`;
    return `↓ E/P thấp hơn lãi suất tiết kiệm. Cổ phiếu kém hấp dẫn hơn tiền gửi.`;
}

function getProgressInsight(type, score) {
    if (type === 'profit') return score >= 15 ? 'Tăng trưởng ổn định' : (score > 0 ? 'Có tăng trưởng' : 'Suy giảm');
    if (type === 'roe') return score >= 15 ? 'Hiệu quả cao (>17%)' : (score > 0 ? 'Trung bình' : 'Kém hiệu quả');
    if (type === 'pe') return score >= 15 ? 'Định giá rẻ' : (score > 0 ? 'Định giá hợp lý' : 'Định giá cao');
    if (type === 'eps') return score >= 15 ? 'Sinh lời mạnh (>3000đ)' : (score > 0 ? 'Sinh lời trung bình' : 'Thấp');
    if (type === 'ep') return score >= 15 ? 'Hấp dẫn hơn LS' : (score > 0 ? 'Tương đương LS' : 'Kém hơn LS');
    return '';
}

function renderProgressBar(label, score, maxScore, insight) {
    let color = 'var(--fd-negative)';
    if (score >= 15) color = 'var(--fd-positive)';
    else if (score >= 8) color = 'var(--fd-warning)';
    
    let pct = (score / maxScore) * 100;
    return `
        <div class="fd-progress-item">
            <div class="fd-progress-label">${label}</div>
            <div class="fd-progress-bar-container">
                <div class="fd-progress-bar-fill" style="width: ${pct}%; background: ${color};"></div>
            </div>
            <div class="fd-progress-score">${score}/${maxScore}</div>
            <div class="fd-progress-insight" style="color: ${color}">${insight}</div>
        </div>
    `;
}


function renderFundamental() {
    const sec = document.getElementById('fundamentalSection');
    if (!sec || !f_data) return;
    
    const data = getAlignedData(); // Cho hiển thị biểu đồ và bảng
    const quarterlyData = getAlignedData('quarterly'); // Cố định dùng dữ liệu Quý cho Chấm điểm
    
    if (data.length === 0 || quarterlyData.length === 0) {
        sec.innerHTML = `
            <div class="fd-header">
                <div class="fd-title">📊 Đánh giá cơ bản: ${f_symbol}</div>
            </div>
            <div style="padding: 40px; text-align: center; color: #888;">
                <div style="font-size: 32px; margin-bottom: 16px;">📭</div>
                <div>Không có dữ liệu tài chính cho mã này.</div>
                <div style="font-size: 13px; margin-top: 8px;">(Lưu ý: Các chỉ số thị trường không có báo cáo tài chính)</div>
            </div>
        `;
        return;
    }
    
    const ratesInfo = processInterestRates();
    const scoreInfo = calculateScore(quarterlyData, ratesInfo);
    
    Object.keys(f_charts).forEach(k => {
        if (f_charts[k]) f_charts[k].destroy();
    });
    f_charts = {};
    
    const html = `
        <div class="fd-header">
            <div class="fd-title">📊 Đánh giá cơ bản: ${f_symbol}</div>
            <div class="fd-time-toggle">
                <button class="fd-time-btn ${f_mode==='quarterly'?'active':''}" onclick="setFundamentalMode('quarterly')">12 Quý</button>
                <button class="fd-time-btn ${f_mode==='yearly'?'active':''}" onclick="setFundamentalMode('yearly')">5 Năm</button>
            </div>
            <button class="fd-close-btn" onclick="window.openFinancialModal()">✕</button>
        </div>
        
        <div class="fd-verdict-container">
            <div class="fd-score-box">
                <div class="fd-score-circle">
                    <svg>
                        <circle class="bg" cx="60" cy="60" r="40"></circle>
                        <circle class="progress" cx="60" cy="60" r="40" id="fdScoreCircle"></circle>
                    </svg>
                    <div class="fd-score-text">
                        <div class="val" id="fdScoreText">0</div>
                        <div class="lbl">Điểm</div>
                    </div>
                </div>
            </div>
            <div class="fd-verdict-box">
                <div class="fd-verdict-header">
                    <div class="fd-verdict-badge" style="background: ${scoreInfo.color}20; color: ${scoreInfo.color}; border: 1px solid ${scoreInfo.color}50;">
                        ĐÁNH GIÁ: ${scoreInfo.verdict}
                    </div>
                    <div class="fd-verdict-badge" style="background: var(--fd-bg); color: ${scoreInfo.typeColor}; border: 1px solid var(--fd-border);">
                        PHÂN LOẠI: ${scoreInfo.type}
                    </div>
                </div>
                
                <div class="fd-progress-list">
                    ${renderProgressBar('Tăng trưởng Lợi nhuận', scoreInfo.breakdown.scoreProfit, 20, getProgressInsight('profit', scoreInfo.breakdown.scoreProfit))}
                    ${renderProgressBar('Hiệu quả sử dụng Vốn', scoreInfo.breakdown.scoreROE, 20, getProgressInsight('roe', scoreInfo.breakdown.scoreROE))}
                    ${renderProgressBar('Mặt bằng Định giá', scoreInfo.breakdown.scorePE, 20, getProgressInsight('pe', scoreInfo.breakdown.scorePE))}
                    ${renderProgressBar('Sức mạnh Thu nhập', scoreInfo.breakdown.scoreEPS, 20, getProgressInsight('eps', scoreInfo.breakdown.scoreEPS))}
                    ${renderProgressBar('Tỷ suất Sinh lời thực tế', scoreInfo.breakdown.scoreEP, 20, getProgressInsight('ep', scoreInfo.breakdown.scoreEP))}
                </div>

            </div>
        </div>
        
        <div class="fd-charts-grid">
            ${createChartCard('chart1', '1. Tăng trưởng Lợi nhuận (%)', getInsightYoY(data), 'chart1')}
            ${createChartCard('chart2', '2. Quy mô Doanh thu & LNST', getInsightScale(data), 'chart2')}
            ${createChartCard('chart3', '3. Hiệu quả sử dụng Vốn', getInsightROE(data), 'chart3')}
            ${createChartCard('chart4', '4. Mặt bằng Định giá', getInsightValuation(data), 'chart4')}
            ${createChartCard('chart5', '5. Sức mạnh Thu nhập', getInsightEPS(data), 'chart5')}
            ${createChartCard('chart6', '6. Tỷ suất Sinh lời thực tế', getInsightEP(data, ratesInfo), 'chart6')}
        </div>
        
        <div class="fd-table-container">
            <table class="fd-table" id="fdTable">
                <thead><tr id="fdTableHead"></tr></thead>
                <tbody id="fdTableBody"></tbody>
            </table>
            <div style="padding: 10px; font-size: 12px; color: var(--fd-text-dim);">
                Ô "--" là quý nguồn chưa công bố. Xu hướng LN so sánh %YoY LN kỳ này vs kỳ trước.
            </div>
        </div>
    `;
    
    sec.innerHTML = html;
    
    setTimeout(() => {
        const circle = document.getElementById('fdScoreCircle');
        const text = document.getElementById('fdScoreText');
        if (circle) {
            const offset = 251 - (251 * scoreInfo.score) / 100;
            circle.style.strokeDashoffset = offset;
            circle.style.stroke = scoreInfo.color;
        }
        if (text) {
            let curr = 0;
            if (scoreInfo.score === 0) { text.innerText = '0'; return; }
            const timer = setInterval(() => {
                curr += 2;
                if (curr >= scoreInfo.score) {
                    curr = scoreInfo.score;
                    clearInterval(timer);
                }
                text.innerText = curr;
            }, 20);
        }
    }, 100);
    
    renderCharts(data, ratesInfo);
    renderTable(data);
}

function createChartCard(id, title, insightText, themeKey) {
    let icon = '💡';
    if (insightText.startsWith('↑')) icon = '🟢';
    else if (insightText.startsWith('↓')) icon = '🔴';
    else if (insightText.startsWith('→')) icon = '🟡';
    
    let cleanText = insightText;
    if (icon !== '💡') cleanText = insightText.substring(2);

    return `
        <div class="fd-chart-card" style="border-left: 4px solid var(--${themeKey}-accent);">
            <div class="fd-chart-header" style="background: var(--${themeKey}-bg);">
                <div class="fd-chart-title">${title}</div>
            </div>
            <div class="fd-chart-canvas-container">
                <canvas id="${id}"></canvas>
            </div>
            <div class="fd-insight-box" style="border-left: 3px solid var(--${themeKey}-accent);">
                <div class="fd-insight-icon">${icon}</div>
                <div class="fd-insight-text">${cleanText}</div>
            </div>
        </div>
    `;
}

function formatB(val) {
    if (!val) return '0';
    return (val / 1e9).toFixed(1) + ' tỷ';
}

function formatPct(val) {
    if (val === null || val === undefined) return '0%';
    return (val * 100).toFixed(1) + '%';
}

function renderCharts(data, ratesInfo) {
    // Tự động giới hạn số lượng kỳ hiển thị trên biểu đồ (12 quarters)
    let displayData = [...data];
    if (f_mode === 'quarterly' && displayData.length > 12) {
        displayData = displayData.slice(displayData.length - 12);
    }
    if (f_mode === 'yearly' && displayData.length > 5) {
        displayData = displayData.slice(displayData.length - 5);
    }
    
    const labels = displayData.map(d => d.label);
    const textColor = document.body.classList.contains('light-theme') ? '#666' : '#a3a6af';
    const gridColor = document.body.classList.contains('light-theme') ? '#e9ecef' : '#2a2e39';
    
    const commonOptions = {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 600 },
        interaction: { mode: 'index', intersect: false },
        plugins: {
            legend: { position: 'top', labels: { color: textColor, boxWidth: 12 } },
            tooltip: { 
                mode: 'index', 
                intersect: false,
                backgroundColor: 'rgba(19,23,34,0.95)',
                titleColor: '#fff',
                bodyColor: '#d1d4dc',
                borderColor: '#2a2e39',
                borderWidth: 1,
                cornerRadius: 8,
                padding: 10
            }
        },
        scales: {
            x: { ticks: { color: textColor }, grid: { color: gridColor, drawBorder: false } },
            y: { ticks: { color: textColor }, grid: { color: gridColor, drawBorder: false } }
        }
    };

    // 1. Tăng trưởng YoY (Hai Cột)
    const ctx1 = document.getElementById('chart1').getContext('2d');
    f_charts['chart1'] = new Chart(ctx1, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { 
                    label: '%YoY Doanh thu', 
                    data: displayData.map(d => d.revYoY * 100), 
                    backgroundColor: displayData.map(d => d.revYoY >= 0 ? '#8fb0fb' : '#2962ff'),
                    borderRadius: 4
                },
                { 
                    label: '%YoY Lợi nhuận', 
                    data: displayData.map(d => d.profitYoY * 100), 
                    backgroundColor: displayData.map(d => d.profitYoY >= 0 ? '#85d199' : '#16a34a'),
                    borderRadius: 4
                }
            ]
        },
        options: commonOptions
    });

    // 2. Quy mô DT & LNST (Cột) + Tăng trưởng LNST (Đường)
    const ctx2 = document.getElementById('chart2').getContext('2d');
    f_charts['chart2'] = new Chart(ctx2, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { 
                    label: 'Doanh thu (tỷ)', 
                    data: displayData.map(d => d.revenue / 1e9), 
                    backgroundColor: '#d66058', 
                    order: 2,
                    yAxisID: 'y'
                },
                { 
                    label: 'Lợi nhuận (tỷ)', 
                    data: displayData.map(d => d.profit / 1e9), 
                    backgroundColor: '#6b8e8e', 
                    order: 2,
                    yAxisID: 'y'
                },
                { 
                    label: 'Tăng trưởng LNST (%)', 
                    data: displayData.map(d => d.profitYoY * 100), 
                    type: 'line', 
                    borderColor: '#ffee00', 
                    backgroundColor: '#ffee00',
                    borderWidth: 2, 
                    pointRadius: 4,
                    order: 1,
                    yAxisID: 'y1'
                }
            ]
        },
        options: {
            ...commonOptions,
            scales: {
                x: commonOptions.scales.x,
                y: { type: 'linear', display: true, position: 'left', ticks: { color: textColor }, grid: { color: gridColor } },
                y1: { type: 'linear', display: true, position: 'right', ticks: { color: '#ffee00' }, grid: { drawOnChartArea: false } }
            }
        }
    });

    // 3. ROE
    const avgRoe = displayData.reduce((a,b)=>a+b.roe,0)/displayData.length * 100;
    const ctx3 = document.getElementById('chart3').getContext('2d');
    f_charts['chart3'] = new Chart(ctx3, {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'ROE (%)', data: displayData.map(d => d.roe * 100), borderColor: '#00bcd4', backgroundColor: 'rgba(0,188,212,0.1)', fill: true, tension: 0.3 },
                { label: 'ROE TB', data: Array(labels.length).fill(avgRoe), borderColor: '#9e9e9e', borderDash: [5,5], pointRadius: 0 }
            ]
        },
        options: commonOptions
    });

    // 4. P/E & P/B
    const ctx4 = document.getElementById('chart4').getContext('2d');
    f_charts['chart4'] = new Chart(ctx4, {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'P/E', data: displayData.map(d => d.pe), borderColor: '#9c27b0', yAxisID: 'y' },
                { label: 'P/B', data: displayData.map(d => d.pb), borderColor: '#e91e63', yAxisID: 'y1' }
            ]
        },
        options: {
            ...commonOptions,
            scales: {
                x: commonOptions.scales.x,
                y: { type: 'linear', display: true, position: 'left', ticks: { color: '#9c27b0' }, grid: { color: gridColor } },
                y1: { type: 'linear', display: true, position: 'right', ticks: { color: '#e91e63' }, grid: { drawOnChartArea: false } }
            }
        }
    });

    // 5. EPS & Tăng trưởng EPS
    const ctx5 = document.getElementById('chart5').getContext('2d');
    f_charts['chart5'] = new Chart(ctx5, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { 
                    label: 'EPS (VNĐ)', 
                    data: displayData.map(d => d.eps), 
                    backgroundColor: '#3f51b5', 
                    yAxisID: 'y',
                    order: 2,
                    barPercentage: 0.6
                },
                { 
                    label: '%YoY EPS', 
                    data: displayData.map(d => d.epsYoY * 100), 
                    type: 'line', 
                    borderColor: '#ffee00', 
                    backgroundColor: '#ffee00',
                    borderWidth: 2,
                    pointRadius: 4,
                    yAxisID: 'y1',
                    order: 1
                }
            ]
        },
        options: {
            ...commonOptions,
            scales: {
                x: commonOptions.scales.x,
                y: { type: 'linear', display: true, position: 'left', ticks: { color: '#3f51b5' }, grid: { color: gridColor } },
                y1: { type: 'linear', display: true, position: 'right', ticks: { color: '#ffee00' }, grid: { drawOnChartArea: false } }
            }
        }
    });

    // 6. E/P vs Lãi suất
    const epData = displayData.map(d => d.pe > 0 ? (1 / d.pe) * 100 : 0);
    const ctx6 = document.getElementById('chart6').getContext('2d');
    f_charts['chart6'] = new Chart(ctx6, {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'E/P (%)', data: epData, borderColor: '#2563EB', backgroundColor: 'rgba(37,99,235,0.1)', fill: true, borderWidth: 3 },
                { label: 'VCB 12T', data: Array(labels.length).fill(ratesInfo.vcb), borderColor: '#16A34A', pointRadius: 0 },
                { label: 'TCB 12T', data: Array(labels.length).fill(ratesInfo.tcb), borderColor: '#D97706', pointRadius: 0 },
                { label: 'Lãi suất Max', data: Array(labels.length).fill(ratesInfo.maxRate), borderColor: '#DC2626', borderDash: [5, 5], pointRadius: 0 },
                { label: 'Lãi suất TB', data: Array(labels.length).fill(ratesInfo.avgRate), borderColor: '#7C3AED', borderDash: [5, 5], pointRadius: 0 }
            ]
        },
        options: commonOptions
    });
}

function renderTable(data) {
    const head = document.getElementById('fdTableHead');
    const body = document.getElementById('fdTableBody');
    
    let displayData = [...data];
    if (f_mode === 'quarterly' && displayData.length > 12) displayData = displayData.slice(displayData.length - 12);
    if (f_mode === 'yearly' && displayData.length > 5) displayData = displayData.slice(displayData.length - 5);
    
    let hHTML = '<th>Chỉ tiêu</th>';
    displayData.forEach((d, idx) => { 
        let isLatest = idx === displayData.length - 1;
        hHTML += `<th class="${isLatest ? 'col-latest' : ''}">${d.label}${isLatest ? ' ★' : ''}</th>`; 
    });
    head.innerHTML = hHTML;
    
    const rows = [
        { label: 'Quy mô', isGroup: true },
        { label: 'DT (tỷ)', fn: d => (d.revenue/1e9).toFixed(1) },
        { label: 'LNST (tỷ)', fn: d => (d.profit/1e9).toFixed(1) },
        { label: 'Tăng trưởng', isGroup: true },
        { label: '%YoY DT', fn: d => `<span class="${d.revYoY>=0?'val-pos':'val-neg'}">${d.revYoY>0?'+':''}${formatPct(d.revYoY)}</span>` },
        { label: '%YoY LN', fn: d => `<span class="${d.profitYoY>=0?'val-pos':'val-neg'}">${d.profitYoY>0?'+':''}${formatPct(d.profitYoY)}</span>` },
        { label: 'Xu hướng LN', fn: d => {
            if (d.profitYoY > 0) return `<span class="fd-chip up">Tăng tốc ▲</span>`;
            if (d.profitYoY < 0) return `<span class="fd-chip down">Giảm tốc ▼</span>`;
            return '--';
        }},
        { label: 'Hiệu quả', isGroup: true },
        { label: 'ROE (%)', fn: d => `<span class="${d.roe>=0?'val-pos':'val-neg'}">${d.roe>0?'+':''}${formatPct(d.roe)}</span>` },
        { label: 'EPS (đồng)', fn: d => d.eps.toFixed(0) },
        { label: '%YoY EPS', fn: d => `<span class="${d.epsYoY>=0?'val-pos':'val-neg'}">${d.epsYoY>0?'+':''}${formatPct(d.epsYoY)}</span>` },
        { label: 'Định giá', isGroup: true },
        { label: 'P/E', fn: d => d.pe.toFixed(2) },
        { label: 'P/B', fn: d => d.pb.toFixed(2) }
    ];
    
    let bHTML = '';
    rows.forEach(r => {
        if (r.isGroup) {
            bHTML += `<tr class="fd-row-group"><td colspan="${displayData.length + 1}" style="padding: 8px 12px; font-weight: bold; color: var(--fd-accent); border-bottom: 1px solid var(--fd-border);">${r.label}</td></tr>`;
        } else {
            bHTML += `<tr><td>${r.label}</td>`;
            displayData.forEach((d, idx) => { 
                let isLatest = idx === displayData.length - 1;
                bHTML += `<td class="${isLatest ? 'col-latest' : ''}">${r.fn(d)}</td>`; 
            });
            bHTML += `</tr>`;
        }
    });
    
    body.innerHTML = bHTML;
}
