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
    
    sec.innerHTML = `
        <div class="fd-header">
            <div class="fd-title">📊 Đánh giá cơ bản: ${f_symbol}</div>
        </div>
        <div style="padding: 40px; text-align: center; color: #888;">Đang tải dữ liệu tài chính...</div>
    `;
    
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
            </div>
            <div style="padding: 40px; text-align: center; color: #f44336;">Lỗi tải dữ liệu: ${e.message}</div>
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

// Insight Engine Rule-based
function getInsightYoY(data) {
    if (data.length < 2) return '';
    const latest = data[data.length - 1];
    const prev = data[data.length - 2];
    
    let streak = 0;
    for (let i = data.length - 1; i > 0; i--) {
        if (data[i].profit > data[i-1].profit) streak++;
        else break;
    }
    
    let msg = `LNST ${latest.label} đạt ${formatB(latest.profit)}, `;
    if (latest.profitYoY > 0) {
        msg += `tăng mạnh ${(latest.profitYoY * 100).toFixed(1)}% so với cùng kỳ. `;
        if (streak >= 2) msg += `Doanh nghiệp ghi nhận ${streak} kỳ tăng trưởng liên tiếp, cho thấy đà tăng trưởng đang rất vững chắc.`;
    } else {
        msg += `giảm ${(Math.abs(latest.profitYoY) * 100).toFixed(1)}% so với cùng kỳ. Động lực tăng trưởng đang suy yếu.`;
    }
    return msg;
}

function getInsightScale(data) {
    if (data.length < 2) return '';
    const latest = data[data.length - 1];
    const maxRev = Math.max(...data.map(d => d.revenue));
    
    let msg = `Quy mô doanh thu duy trì ở mức ${formatB(latest.revenue)}. `;
    if (latest.revenue >= maxRev) {
        msg += `Đạt mức cao kỷ lục trong giai đoạn khảo sát. Quy mô hoạt động mở rộng tích cực.`;
    } else {
        msg += `Chưa phá được đỉnh cũ ${formatB(maxRev)}.`;
    }
    return msg;
}

function getInsightROE(data) {
    if (data.length < 2) return '';
    const latest = data[data.length - 1];
    const avg = data.reduce((a,b) => a + b.roe, 0) / data.length;
    
    let msg = `ROE đạt ${(latest.roe * 100).toFixed(1)}%, `;
    if (latest.roe > avg) msg += `cao hơn mức trung bình dài hạn (${(avg * 100).toFixed(1)}%). Hiệu quả sử dụng vốn đang cải thiện rõ rệt.`;
    else msg += `thấp hơn mức trung bình (${(avg * 100).toFixed(1)}%). Cần theo dõi hiệu quả kinh doanh.`;
    return msg;
}

function getInsightValuation(data) {
    if (data.length < 2) return '';
    const latest = data[data.length - 1];
    const avgPE = data.reduce((a,b) => a + b.pe, 0) / data.length;
    
    let msg = `P/E hiện tại là ${latest.pe.toFixed(1)}x. `;
    if (latest.pe < avgPE) msg += `Mức định giá đang rẻ hơn trung bình lịch sử (${avgPE.toFixed(1)}x), tạo ra biên an toàn tốt cho nhà đầu tư.`;
    else msg += `Định giá đắt hơn trung bình lịch sử (${avgPE.toFixed(1)}x).`;
    return msg;
}

function getInsightEPS(data) {
    if (data.length < 2) return '';
    const latest = data[data.length - 1];
    let msg = `EPS đạt ${latest.eps.toFixed(0)} đ/cp. `;
    if (latest.epsYoY > 0) msg += `Tăng trưởng dương, tạo cơ sở vững chắc cho giá cổ phiếu.`;
    else msg += `Tăng trưởng âm, gây áp lực lên định giá.`;
    return msg;
}

function getInsightEP(data, ratesInfo) {
    if (data.length < 1) return '';
    const latest = data[data.length - 1];
    const ep = latest.pe > 0 ? (1 / latest.pe) * 100 : 0;
    
    let msg = `Tỷ suất lợi tức E/P đạt ${ep.toFixed(1)}%. `;
    if (ep > ratesInfo.maxRate) msg += `Vượt trội so với lãi suất cao nhất thị trường (${ratesInfo.maxRate.toFixed(1)}%). Dòng tiền kinh doanh sinh lời tốt hơn việc gửi tiết kiệm.`;
    else if (ep > ratesInfo.avgRate) msg += `Tốt hơn mức lãi suất tiết kiệm trung bình (${ratesInfo.avgRate.toFixed(1)}%).`;
    else msg += `Thấp hơn lãi suất tiết kiệm. Cổ phiếu đang kém hấp dẫn hơn kênh tiền gửi an toàn.`;
    return msg;
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
        <div class="fd-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
            <div class="fd-title" style="font-size: 20px; font-weight: bold; color: #d1d4dc;">📊 Đánh giá cơ bản: ${f_symbol}</div>
            <div class="fd-time-toggle" style="display: flex; gap: 10px;">
                <button class="fd-time-btn ${f_mode==='quarterly'?'active':''}" onclick="setFundamentalMode('quarterly')" style="padding: 6px 12px; border-radius: 4px; border: none; cursor: pointer; background: ${f_mode==='quarterly'?'#2962ff':'#2a2e39'}; color: white;">12 Quý</button>
                <button class="fd-time-btn ${f_mode==='yearly'?'active':''}" onclick="setFundamentalMode('yearly')" style="padding: 6px 12px; border-radius: 4px; border: none; cursor: pointer; background: ${f_mode==='yearly'?'#2962ff':'#2a2e39'}; color: white;">5 Năm</button>
            </div>
        </div>
        
        <div class="fd-verdict-container" style="display: flex; align-items: stretch; background: #1e222d; border-radius: 8px; margin-bottom: 24px; border: 1px solid #2a2e39;">
            <div class="fd-score-box" style="padding: 24px; border-right: 1px solid #2a2e39; display: flex; align-items: center; justify-content: center; width: 150px;">
                <div class="fd-score-circle" style="position: relative; width: 100px; height: 100px;">
                    <svg style="transform: rotate(-90deg); width: 100px; height: 100px;">
                        <circle class="bg" cx="50" cy="50" r="40" style="fill: none; stroke: #2a2e39; stroke-width: 8;"></circle>
                        <circle class="progress" cx="50" cy="50" r="40" id="fdScoreCircle" style="fill: none; stroke-width: 8; stroke-dasharray: 251; stroke-dashoffset: 251; transition: stroke-dashoffset 1s ease-out; stroke-linecap: round;"></circle>
                    </svg>
                    <div class="fd-score-text" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                        <div class="val" id="fdScoreText" style="font-size: 24px; font-weight: bold; color: #fff;">0</div>
                        <div class="lbl" style="font-size: 11px; color: #787b86;">Điểm</div>
                    </div>
                </div>
            </div>
            <div class="fd-verdict-box" style="padding: 24px; flex: 1;">
                <div class="fd-verdict-header" style="margin-bottom: 12px; display: flex; gap: 12px; flex-wrap: wrap;">
                    <div class="fd-verdict-badge" style="display: inline-block; padding: 6px 12px; border-radius: 4px; font-weight: bold; background: ${scoreInfo.color}20; color: ${scoreInfo.color}; border: 1px solid ${scoreInfo.color}50;">
                        ĐÁNH GIÁ: ${scoreInfo.verdict}
                    </div>
                    <div class="fd-verdict-badge" style="display: inline-block; padding: 6px 12px; border-radius: 4px; font-weight: bold; background: #2a2e39; color: ${scoreInfo.typeColor}; border: 1px solid #363c4e;">
                        PHÂN LOẠI: ${scoreInfo.type}
                    </div>
                </div>
                <div class="fd-verdict-desc" style="display:flex; flex-wrap:wrap; gap:16px; color: #d1d4dc; font-size: 14px;">
                    <div><span style="color:#787b86">Tăng trưởng LN:</span> <b>${scoreInfo.breakdown.scoreProfit}/20</b></div>
                    <div><span style="color:#787b86">ROE:</span> <b>${scoreInfo.breakdown.scoreROE}/20</b></div>
                    <div><span style="color:#787b86">P/E:</span> <b>${scoreInfo.breakdown.scorePE}/20</b></div>
                    <div><span style="color:#787b86">EPS:</span> <b>${scoreInfo.breakdown.scoreEPS}/20</b></div>
                    <div><span style="color:#787b86">E/P vs Lãi suất:</span> <b>${scoreInfo.breakdown.scoreEP}/20</b></div>
                </div>
                <div class="fd-verdict-desc" style="margin-top:12px; font-size:13px; color:#DC2626;">
                    *(Nguyên tắc Veto: Đánh giá TRUNG LẬP nếu có bất kỳ tiêu chí nào 0 điểm)*
                </div>
                <div style="margin-top:4px; font-size:12px; color:#787b86;">
                    *(Thang P/E áp dụng cho cổ phiếu giá trị. Cổ phiếu tăng trưởng cao có P/E > 10 là bình thường, nên có thể nhận điểm thấp ở tiêu chí này)*
                </div>
            </div>
        </div>
        
        <div class="fd-charts-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; margin-bottom: 24px;">
            ${createChartCard('chart1', '1. Tăng trưởng Lợi nhuận YoY (%)', getInsightYoY(data))}
            ${createChartCard('chart2', '2. Quy mô Doanh thu & LNST', getInsightScale(data))}
            ${createChartCard('chart3', '3. Hiệu quả sinh lời (ROE)', getInsightROE(data))}
            ${createChartCard('chart4', '4. Định giá P/E & P/B', getInsightValuation(data))}
            ${createChartCard('chart5', '5. EPS & Tăng trưởng EPS', getInsightEPS(data))}
            ${createChartCard('chart6', '6. E/P vs Lãi suất Tiết kiệm', getInsightEP(data, ratesInfo))}
        </div>
        
        <div class="fd-table-container" style="overflow-x: auto; background: #1e222d; border-radius: 8px; border: 1px solid #2a2e39;">
            <table class="fd-table" id="fdTable" style="width: 100%; border-collapse: collapse; color: #d1d4dc; font-size: 13px; white-space: nowrap;">
                <thead><tr id="fdTableHead" style="background: #2a2e39; text-align: right;"></tr></thead>
                <tbody id="fdTableBody"></tbody>
            </table>
            <div style="padding: 10px; font-size: 12px; color: #787b86;">
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

function createChartCard(id, title, insightText) {
    return `
        <div class="fd-chart-card" style="background: #1e222d; border-radius: 8px; padding: 16px; border: 1px solid #2a2e39; display: flex; flex-direction: column;">
            <div class="fd-chart-header" style="margin-bottom: 12px; font-weight: bold; color: #d1d4dc;">
                <div class="fd-chart-title">${title}</div>
            </div>
            <div class="fd-chart-canvas-container" style="height: 250px; position: relative;">
                <canvas id="${id}"></canvas>
            </div>
            <div class="fd-insight-box" style="margin-top: 16px; background: rgba(41, 98, 255, 0.1); padding: 12px; border-radius: 6px; border-left: 4px solid #2962ff; font-size: 13px; color: #d1d4dc; line-height: 1.4;">
                💡 ${insightText}
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
        plugins: {
            legend: { position: 'top', labels: { color: textColor, boxWidth: 12 } },
            tooltip: { mode: 'index', intersect: false }
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
    
    let hHTML = '<th style="position: sticky; left: 0; background: #2a2e39; z-index: 2; text-align: left; padding: 10px; border-bottom: 1px solid #363c4e;">Chỉ tiêu</th>';
    displayData.forEach(d => { 
        hHTML += `<th style="padding: 10px; border-bottom: 1px solid #363c4e; text-align: right;">${d.label}</th>`; 
    });
    head.innerHTML = hHTML;
    
    const rows = [
        { label: 'DT (tỷ)', fn: d => (d.revenue/1e9).toFixed(1) },
        { label: 'LNST (tỷ)', fn: d => (d.profit/1e9).toFixed(1) },
        { label: '%YoY DT', fn: d => `<span style="color: ${d.revYoY>=0?'#16A34A':'#DC2626'}">${d.revYoY>0?'+':''}${formatPct(d.revYoY)}</span>` },
        { label: '%YoY LN', fn: d => `<span style="color: ${d.profitYoY>=0?'#16A34A':'#DC2626'}">${d.profitYoY>0?'+':''}${formatPct(d.profitYoY)}</span>` },
        { label: 'ROE (%)', fn: d => `<span style="color: ${d.roe>=0?'#16A34A':'#DC2626'}">${d.roe>0?'+':''}${formatPct(d.roe)}</span>` },
        { label: 'EPS (đồng)', fn: d => d.eps.toFixed(0) },
        { label: '%YoY EPS', fn: d => `<span style="color: ${d.epsYoY>=0?'#16A34A':'#DC2626'}">${d.epsYoY>0?'+':''}${formatPct(d.epsYoY)}</span>` },
        { label: 'Xu hướng LN', fn: d => {
            if (d.profitYoY > 0) return `<span style="background: #DCFCE7; color: #16A34A; padding: 2px 6px; border-radius: 4px;">Tăng tốc ▲</span>`;
            if (d.profitYoY < 0) return `<span style="background: #FEE2E2; color: #DC2626; padding: 2px 6px; border-radius: 4px;">Giảm tốc ▼</span>`;
            return '--';
        }},
        { label: 'P/E', fn: d => d.pe.toFixed(2) },
        { label: 'P/B', fn: d => d.pb.toFixed(2) }
    ];
    
    let bHTML = '';
    rows.forEach(r => {
        bHTML += `<tr><td style="position: sticky; left: 0; background: #1e222d; z-index: 1; padding: 10px; border-bottom: 1px solid #2a2e39; font-weight: bold; text-align: left;">${r.label}</td>`;
        displayData.forEach((d, idx) => { 
            let bg = idx === displayData.length - 1 ? 'background: #2a2e39;' : '';
            bHTML += `<td style="padding: 10px; border-bottom: 1px solid #2a2e39; text-align: right; ${bg}">${r.fn(d)}</td>`; 
        });
        bHTML += `</tr>`;
    });
    
    body.innerHTML = bHTML;
}
