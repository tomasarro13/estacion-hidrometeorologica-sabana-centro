#pragma once
// Tablero embebido. Sin CDN ni librerías externas: la WLAN no tiene Internet.
// La gráfica se dibuja como SVG generado en el navegador.

const char PAGINA_HTML[] PROGMEM = R"HTML(<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Estación hidrometeorológica</title>
<style>
:root{
  --fondo:#0E1620; --panel:#16212E; --borde:#26374A;
  --texto:#E4ECF2; --suave:#8CA3B8;
  --verde:#3DD68C; --amarillo:#F0B429; --rojo:#F2542D; --azul:#4EA8DE;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--fondo);color:var(--texto);
  font-family:system-ui,-apple-system,'Segoe UI',sans-serif;padding:16px}
header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:18px}
h1{font-size:1.15rem;font-weight:600;letter-spacing:-.01em}
.sub{color:var(--suave);font-size:.78rem}
#estado{margin-left:auto;padding:5px 14px;border-radius:999px;
  font-size:.74rem;font-weight:700;letter-spacing:.08em}
.VERDE{background:rgba(61,214,140,.15);color:var(--verde)}
.AMARILLA{background:rgba(240,180,41,.15);color:var(--amarillo)}
.ROJA{background:rgba(242,84,45,.18);color:var(--rojo)}
.FALLA{background:rgba(140,163,184,.15);color:var(--suave)}
.btn{background:var(--panel);border:1px solid var(--borde);color:var(--texto);
  padding:6px 14px;border-radius:7px;font-size:.76rem;cursor:pointer;
  font-family:inherit}
.btn:hover{border-color:var(--azul)}
.btn.sil{background:var(--amarillo);border-color:var(--amarillo);
  color:#1A1203;font-weight:600}

.grid{display:grid;gap:10px;
  grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:18px}
.card{background:var(--panel);border:1px solid var(--borde);
  border-radius:10px;padding:13px 15px}
.card .et{color:var(--suave);font-size:.68rem;text-transform:uppercase;
  letter-spacing:.11em;margin-bottom:7px}
.card .v{font-size:1.75rem;font-weight:650;font-variant-numeric:tabular-nums;
  line-height:1}
.card .u{font-size:.85rem;color:var(--suave);font-weight:400;margin-left:3px}
.card .n{font-size:.68rem;color:var(--suave);margin-top:6px}
.destacada{border-color:var(--azul)}

.bloque{background:var(--panel);border:1px solid var(--borde);
  border-radius:10px;padding:15px;margin-bottom:12px}
.bloque h2{font-size:.72rem;text-transform:uppercase;letter-spacing:.11em;
  color:var(--suave);font-weight:600;margin-bottom:12px}
.tabs{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}
.tab{background:transparent;border:1px solid var(--borde);color:var(--suave);
  padding:4px 11px;border-radius:6px;font-size:.74rem;cursor:pointer}
.tab.on{background:var(--azul);border-color:var(--azul);color:#08121C;font-weight:600}
svg{width:100%;height:190px;display:block}
.ejes{stroke:var(--borde);stroke-width:1}
.serie{fill:none;stroke:var(--azul);stroke-width:2;
  stroke-linejoin:round;stroke-linecap:round}
.marca{fill:var(--suave);font-size:10px}

#banderas{display:flex;gap:6px;flex-wrap:wrap}
.bandera{background:rgba(242,84,45,.14);color:var(--rojo);
  padding:3px 9px;border-radius:5px;font-size:.7rem}
.ok{background:rgba(61,214,140,.13);color:var(--verde)}
.qc{font-size:.7rem;padding:2px 9px;border-radius:5px;margin-left:8px;
  letter-spacing:.04em;font-weight:600}
.qc1{background:rgba(61,214,140,.15);color:var(--verde)}
.qc3{background:rgba(240,180,41,.15);color:var(--amarillo)}
.qc4{background:rgba(242,84,45,.18);color:var(--rojo)}
.qc9{background:rgba(140,163,184,.15);color:var(--suave)}
footer{color:var(--suave);font-size:.7rem;margin-top:14px;
  display:flex;gap:14px;flex-wrap:wrap}
</style></head><body>

<header>
  <h1>Estación hidrometeorológica</h1>
  <span class="sub" id="reloj">—</span>
  <span id="estado" class="VERDE">—</span>
  <button id="btnSil" class="btn" onclick="silenciar()">Silenciar alarma</button>
</header>

<div class="grid">
  <div class="card destacada"><div class="et">Nivel</div>
    <div class="v"><span id="nivel">—</span><span class="u">cm</span></div>
    <div class="n" id="refNivel">—</div></div>
  <div class="card"><div class="et">Caudal</div>
    <div class="v"><span id="caudal">—</span><span class="u">L/min</span></div>
    <div class="n">Curva de gasto</div></div>
  <div class="card destacada"><div class="et">Evaporación</div>
    <div class="v"><span id="evap">—</span><span class="u">mm/día</span></div>
    <div class="n" id="evapH">—</div></div>
  <div class="card"><div class="et">Temperatura</div>
    <div class="v"><span id="temp">—</span><span class="u">°C</span></div>
    <div class="n" id="tempBmp">—</div></div>
  <div class="card"><div class="et">Humedad</div>
    <div class="v"><span id="hr">—</span><span class="u">%</span></div>
    <div class="n">DHT22</div></div>
  <div class="card"><div class="et">Presión</div>
    <div class="v"><span id="pres">—</span><span class="u">hPa</span></div>
    <div class="n" id="notaPres">BMP280 · 2560 m</div></div>
  <div class="card"><div class="et">Luz</div>
    <div class="v"><span id="luz">—</span><span class="u">%</span></div>
    <div class="n">Índice relativo (LDR)</div></div>
</div>

<div class="bloque">
  <h2>Histórico · últimas 24 h</h2>
  <div class="tabs" id="tabs"></div>
  <svg id="graf" viewBox="0 0 700 190" preserveAspectRatio="none"></svg>
</div>

<div class="bloque">
  <h2>Calidad del dato · bandera QARTOD <span id="qcflag"></span></h2>
  <div id="banderas"><span class="bandera ok">Sin observaciones</span></div>
</div>

<div class="bloque">
  <h2>Desempeño de alarmas · EEMUA 191</h2>
  <div class="grid" style="margin:0">
    <div class="card"><div class="et">Tasa actual</div>
      <div class="v"><span id="al10">—</span><span class="u">/10 min</span></div>
      <div class="n">Objetivo &le; 1</div></div>
    <div class="card"><div class="et">Pico</div>
      <div class="v"><span id="alPico">—</span><span class="u">/10 min</span></div>
      <div class="n">Objetivo &lt; 10</div></div>
    <div class="card"><div class="et">Total</div>
      <div class="v"><span id="alTot">—</span></div>
      <div class="n">Desde el arranque</div></div>
  </div>
</div>

<footer>
  <span id="uptime">—</span><span>Servidor embebido en el ESP32 · sin MQTT</span>
</footer>

<script>
const SERIES = [
  {k:'n', n:'Nivel',       u:'cm'},
  {k:'T', n:'Temperatura', u:'°C'},
  {k:'h', n:'Humedad',     u:'%'},
  {k:'e', n:'Evaporación', u:'mm/d'},
  {k:'q', n:'Caudal',      u:'L/min'},
  {k:'l', n:'Luz',         u:'%'}
];
let activa = 'n', hist = [];

const BANDERAS = [
  [0x01,'Nivel fuera de rango'], [0x02,'Salto brusco de nivel'],
  [0x04,'Sin eco del ultrasónico'], [0x08,'Lectura de nivel congelada'],
  [0x10,'Falla del DHT22'], [0x20,'Falla del BMP280'],
  [0x40,'Sol fuerte: temperatura posiblemente sesgada'],
  [0x80,'Descenso no explicado por evaporación']
];

function tabs(){
  const c = document.getElementById('tabs');
  c.innerHTML = '';
  SERIES.forEach(s=>{
    const b = document.createElement('button');
    b.className = 'tab' + (s.k===activa?' on':'');
    b.textContent = s.n;
    b.onclick = ()=>{ activa = s.k; tabs(); dibujar(); };
    c.appendChild(b);
  });
}

function dibujar(){
  const svg = document.getElementById('graf');
  const W = 700, H = 190, mL = 44, mR = 10, mT = 12, mB = 22;
  if (hist.length < 2){
    svg.innerHTML = '<text class="marca" x="350" y="95" text-anchor="middle">'
      + 'Esperando registros (uno cada 5 min)</text>';
    return;
  }
  const vals = hist.map(r=>r[activa]).filter(v=>v!==null && !isNaN(v));
  let min = Math.min(...vals), max = Math.max(...vals);
  if (max - min < 1e-6){ max += 1; min -= 1; }
  const pad = (max-min)*0.1; min -= pad; max += pad;

  const x = i => mL + i*(W-mL-mR)/(hist.length-1);
  const y = v => mT + (H-mT-mB)*(1-(v-min)/(max-min));

  let d = '';
  hist.forEach((r,i)=>{
    const v = r[activa];
    d += (i?' L':'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1);
  });

  const s = SERIES.find(o=>o.k===activa);
  let g = '';
  for (let i=0;i<=4;i++){
    const v = min + (max-min)*i/4, yy = y(v);
    g += `<line class="ejes" x1="${mL}" y1="${yy}" x2="${W-mR}" y2="${yy}"/>`
       + `<text class="marca" x="${mL-6}" y="${yy+3}" text-anchor="end">`
       + v.toFixed(1) + '</text>';
  }
  const hs = (hist.length*5/60).toFixed(1);
  g += `<text class="marca" x="${mL}" y="${H-6}">hace ${hs} h</text>`
     + `<text class="marca" x="${W-mR}" y="${H-6}" text-anchor="end">ahora</text>`
     + `<text class="marca" x="${mL}" y="10">${s.n} (${s.u})</text>`;

  svg.innerHTML = g + `<path class="serie" d="${d}"/>`;
}

function pintarBanderas(q){
  const c = document.getElementById('banderas');
  const act = BANDERAS.filter(b=>q & b[0]);
  c.innerHTML = act.length
    ? act.map(b=>`<span class="bandera">${b[1]}</span>`).join('')
    : '<span class="bandera ok">Sin observaciones</span>';
}

async function refrescar(){
  try{
    const d = await (await fetch('/api/datos')).json();
    const set = (id,v)=>document.getElementById(id).textContent = v;
    set('nivel', d.nivel.toFixed(1));
    set('caudal', d.caudal.toFixed(2));
    set('evap', d.evap.toFixed(2));
    set('temp', d.temp.toFixed(1));
    set('hr', d.hr.toFixed(1));
    set('pres', d.presion.toFixed(1));
    set('notaPres', d.bmpOk ? 'BMP280 · 2560 m'
                            : 'Valor supuesto · sensor ausente');
    set('luz', d.luz.toFixed(0));
    set('refNivel', 'Q80 ' + d.uAmarillo.toFixed(1)
        + ' cm · Q90 ' + d.uRojo.toFixed(1) + ' cm');
    set('evapH', 'Hargreaves ' + d.evapHarg.toFixed(2) + ' mm/día');
    set('tempBmp', 'BMP280 ' + d.tempBmp.toFixed(1) + ' °C');
    const e = document.getElementById('estado');
    e.textContent = d.alerta; e.className = d.alerta;
    pintarBanderas(d.calidad);
    const QC = {1:'1 · PASS',3:'3 · SUSPECT',4:'4 · FAIL',9:'9 · MISSING'};
    const qe = document.getElementById('qcflag');
    qe.textContent = QC[d.qc] || ('' + d.qc);
    qe.className = 'qc qc' + d.qc;
    const b = document.getElementById('btnSil');
    if (d.silenciada) { b.classList.add('sil'); b.textContent = 'Alarma silenciada · reactivar'; }
    else { b.classList.remove('sil'); b.textContent = 'Silenciar alarma'; }
    set('al10', d.alarmas10min.toFixed(1));
    set('alPico', d.alarmasPico);
    set('alTot', d.alarmasTotal);
    const s = d.uptime;
    set('uptime', 'Activa hace ' + Math.floor(s/3600) + ' h '
        + Math.floor((s%3600)/60) + ' min');
    set('reloj', new Date().toLocaleTimeString('es-CO'));
  }catch(err){
    document.getElementById('estado').textContent = 'SIN CONEXIÓN';
  }
}

async function silenciar(){
  const b = document.getElementById('btnSil');
  const reactivar = b.classList.contains('sil');
  try{
    await fetch('/api/silenciar' + (reactivar ? '?on=1' : ''));
    refrescar();
  }catch(err){}
}

async function cargarHistorico(){
  try{ hist = await (await fetch('/api/historico')).json(); dibujar(); }
  catch(err){}
}

tabs();
refrescar(); cargarHistorico();
setInterval(refrescar, 2000);
setInterval(cargarHistorico, 60000);
</script></body></html>)HTML";
