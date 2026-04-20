// 临时验证 HTML 端 schedule->trackPoints 算法是否能正确处理 Python 输出的 schedule JSON
// 用法: node tools/railway/_test_html_logic.js
const fs = require('fs');
const path = require('path');

// ---- 复制自 kmlTrackEditor.html 的核心逻辑 ----
function _scheduleTimeToDate(departDate, dayDiff, hhmm){
  if(!hhmm) return null;
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if(!m) return null;
  const h = parseInt(m[1],10), min = parseInt(m[2],10);
  const dm = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec((departDate||'').trim());
  if(!dm) throw new Error('depart_date 必须形如 2025-01-15');
  const y=+dm[1], mo=+dm[2], d=+dm[3] + (dayDiff||0);
  const t = Date.UTC(y, mo-1, d, h-8, min, 0);
  return new Date(t);
}
function _haversineMeters(lon1, lat1, lon2, lat2){
  const R = 6371008.8;
  const toRad = x => x * Math.PI / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat/2)**2 + Math.cos(toRad(lat1))*Math.cos(toRad(lat2))*Math.sin(dLon/2)**2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}
function _stationAnchor(s, departDate){
  const arr = _scheduleTimeToDate(departDate, s.day_diff, s.arrive_time);
  const dep = _scheduleTimeToDate(departDate, s.day_diff, s.depart_time);
  return { arrive: arr || dep, depart: dep || arr };
}
function _parseScheduleJsonText(text){
  let obj;
  try { obj = JSON.parse(text); }
  catch(e){ throw new Error('JSON 格式错误：' + e.message); }
  if(!obj || typeof obj !== 'object') throw new Error('JSON 根必须是对象');
  if(!obj.depart_date) throw new Error('缺少 depart_date 字段（如 "2025-01-15"）');
  const stations = obj.stations;
  if(!Array.isArray(stations) || stations.length < 2){
    throw new Error('stations 字段缺失或不足 2 个站');
  }
  for(const s of stations){
    if(!s.name && s.station_name) s.name = s.station_name;
    s.arrive_time = s.arrive_time ?? s.arrive ?? null;
    s.depart_time = s.depart_time ?? s.start ?? null;
    const rawDay = s.day_diff ?? s.day ?? 0;
    s.day_diff = (typeof rawDay === 'number') ? rawDay : (parseInt(rawDay, 10) || 0);
    if(s.lon != null) s.lon = Number(s.lon);
    if(s.lat != null) s.lat = Number(s.lat);
  }
  return obj;
}
function _buildTrackPointsFromSchedule(schedule, opts){
  const departDate = schedule.depart_date;
  const stations = schedule.stations.filter(s => Number.isFinite(s.lon) && Number.isFinite(s.lat));
  if(stations.length < 2) throw new Error('至少需要 2 个含坐标的站点');
  const density = opts.density;
  const dwellMode = opts.dwellMode;
  const points = [];
  const anchors = stations.map(s => _stationAnchor(s, departDate));
  for(let i=0;i<anchors.length;i++){
    if(!anchors[i].arrive || !anchors[i].depart){
      throw new Error(`第 ${i+1} 站 "${stations[i].name||''}" 缺少有效时间`);
    }
  }
  for(let i=0;i<stations.length;i++){
    const s = stations[i];
    const a = anchors[i];
    if(dwellMode === 'endpoints'){
      if(a.arrive && a.depart && a.arrive.getTime() !== a.depart.getTime()){
        points.push({ time: a.arrive, lon: s.lon, lat: s.lat, alt: 0, _stationName: s.name });
        points.push({ time: a.depart, lon: s.lon, lat: s.lat, alt: 0, _stationName: s.name });
      }else{
        points.push({ time: a.depart || a.arrive, lon: s.lon, lat: s.lat, alt: 0, _stationName: s.name });
      }
    }else if(dwellMode === 'every-30s'){
      const t0 = a.arrive.getTime(), t1 = a.depart.getTime();
      const step = 30 * 1000;
      if(t1 > t0){
        for(let t=t0; t<=t1; t+=step){
          points.push({ time: new Date(t), lon: s.lon, lat: s.lat, alt: 0, _stationName: s.name });
        }
        if(((t1-t0) % step) !== 0){
          points.push({ time: new Date(t1), lon: s.lon, lat: s.lat, alt: 0, _stationName: s.name });
        }
      }else{
        points.push({ time: new Date(t0), lon: s.lon, lat: s.lat, alt: 0, _stationName: s.name });
      }
    }else{
      points.push({ time: a.depart || a.arrive, lon: s.lon, lat: s.lat, alt: 0, _stationName: s.name });
    }
    if(i < stations.length - 1){
      const sNext = stations[i+1];
      const aNext = anchors[i+1];
      const tStart = a.depart.getTime();
      const tEnd   = aNext.arrive.getTime();
      if(tEnd <= tStart) continue;
      const distM = _haversineMeters(s.lon, s.lat, sNext.lon, sNext.lat);
      const distKm = distM / 1000;
      const n = Math.max(2, Math.ceil(distKm / 100 * density));
      for(let k=1; k<=n; k++){
        const f = k / (n + 1);
        const lon = s.lon + (sNext.lon - s.lon) * f;
        const lat = s.lat + (sNext.lat - s.lat) * f;
        const t   = tStart + (tEnd - tStart) * f;
        points.push({ time: new Date(t), lon, lat, alt: 0 });
      }
    }
  }
  points.sort((a,b) => a.time - b.time);
  const dedup = [];
  for(const p of points){
    const last = dedup[dedup.length-1];
    if(last && last.time.getTime() === p.time.getTime()
       && Math.abs(last.lon - p.lon) < 1e-9
       && Math.abs(last.lat - p.lat) < 1e-9) continue;
    dedup.push(p);
  }
  return dedup.map(p => ({ time: p.time, lon: p.lon, lat: p.lat, alt: p.alt || 0 }));
}

// ---- 测试 ----
const file = path.join(__dirname, 'output', 'G1_test_schedule.json');
const text = fs.readFileSync(file, 'utf-8');

console.log('--- 测试 1: 解析 ---');
const sched = _parseScheduleJsonText(text);
console.log('train_code:', sched.train_code);
console.log('depart_date:', sched.depart_date);
console.log('stations:', sched.stations.length);
console.log('first station:', JSON.stringify({
  name: sched.stations[0].name,
  arrive_time: sched.stations[0].arrive_time,
  depart_time: sched.stations[0].depart_time,
  day_diff: sched.stations[0].day_diff,
  lon: sched.stations[0].lon, lat: sched.stations[0].lat,
}, null, 2));

console.log('\n--- 测试 2: 站间直连 + 默认密度 (density=50) + endpoints dwell ---');
let pts = _buildTrackPointsFromSchedule(sched, { density: 50, dwellMode: 'endpoints' });
console.log('生成轨迹点数:', pts.length);
console.log('首点 :', pts[0].time.toISOString(), pts[0].lon.toFixed(4), pts[0].lat.toFixed(4));
console.log('末点 :', pts[pts.length-1].time.toISOString(), pts[pts.length-1].lon.toFixed(4), pts[pts.length-1].lat.toFixed(4));
// 校验时间单调递增
let ok = true, prev = pts[0].time.getTime();
for(let i=1;i<pts.length;i++){
  if(pts[i].time.getTime() < prev){ ok = false; break; }
  prev = pts[i].time.getTime();
}
console.log('时间是否单调:', ok ? '✅' : '❌');

console.log('\n--- 测试 3: dwell=every-30s, density=20 ---');
pts = _buildTrackPointsFromSchedule(sched, { density: 20, dwellMode: 'every-30s' });
console.log('生成轨迹点数:', pts.length);

console.log('\n--- 测试 4: dwell=none, density=10 ---');
pts = _buildTrackPointsFromSchedule(sched, { density: 10, dwellMode: 'none' });
console.log('生成轨迹点数:', pts.length);

console.log('\n--- 测试 5: 错误处理 (空 stations) ---');
try {
  _buildTrackPointsFromSchedule({ depart_date:'2025-01-15', stations: [{name:'A',lon:1,lat:1,arrive_time:null,depart_time:'09:00',day_diff:0}] }, { density: 10, dwellMode: 'endpoints' });
  console.log('❌ 应抛错但没抛');
} catch(e) {
  console.log('✅ 正确抛错:', e.message);
}

console.log('\n--- 测试 6: 跨日 (day=1) ---');
const sched2 = JSON.parse(text);
sched2.stations[6].day = 1; // 末站延后一天
const sched2Norm = _parseScheduleJsonText(JSON.stringify(sched2));
const pts2 = _buildTrackPointsFromSchedule(sched2Norm, { density: 10, dwellMode: 'none' });
console.log('生成轨迹点数:', pts2.length);
console.log('末点 (跨日):', pts2[pts2.length-1].time.toISOString(), '应该是 2026-04-22 03:24Z (即 2026-04-22 11:24+08)');
