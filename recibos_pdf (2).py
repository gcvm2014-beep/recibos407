#!/usr/bin/env python3
"""
recibos_pdf.py — Decreto 407/2026 / Ley 27.802 — Anexo III
Uso: python recibos_pdf.py entrada.txt salida.pdf
"""
import sys, re, os, tempfile
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas

# ── Mapeo código → rubro oficial Anexo III ──────────────────
RUBRO = {
    '800': 'Seguridad Social',
    '801': 'Seguridad Social',
    '802': 'Seguridad Social',
    '803': 'INSSJP',
    '804': 'Obra Social',
    '805': 'Obra Social',
    '806': 'Obra Social',
    '807': 'Sindical',       # Aporte 1% Vigilancia → Sindical
    '808': 'Sindical',
    '809': 'Sindical',
    '816': 'ART',
    '817': 'ART',
    '818': 'ART',
    '834': 'Otros',
    '835': 'Otros',
}

# Códigos NO remunerativos (no van al sueldo bruto)
CODIGOS_NO_REM = {'523','533','534','535','536','537','538','539'}

COLORES = {
    'Sueldo Neto':      '#546E7A',
    'Seguridad Social': '#1565C0',
    'Obra Social':      '#2E7D32',
    'INSSJP':           '#E65100',
    'ART':              '#B71C1C',
    'Sindical':         '#7B1FA2',
    'Cámaras':          '#37474F',
    'Otros':            '#795548',
}

# ── Utilidades ───────────────────────────────────────────────
def pm(txt):
    t = re.sub(r'[^\d,]','', txt.strip())
    if not t: return 0.0
    t = t.replace(',','.')
    # Si hay más de un punto, el último es decimal
    parts = t.split('.')
    if len(parts) > 2:
        t = ''.join(parts[:-1]) + '.' + parts[-1]
    try: return float(t)
    except: return 0.0

def fm(v):
    if v == 0: return '$0,00'
    neg = v < 0
    p = f"{abs(v):,.2f}".split('.')
    r = ('−$' if neg else '$') + p[0].replace(',','.') + ',' + p[1]
    return r

def antiguedad(fi):
    if not fi: return '—'
    try:
        parts = fi.split('/')
        a = int(parts[2]); a = a+2000 if a<50 else (a+1900 if a<100 else a)
        from datetime import date
        return f"{date.today().year - a} año/s"
    except: return '—'

# ── Parser ───────────────────────────────────────────────────
def parsear_bloque(lineas):
    """Parsea un bloque (página COBOL) y devuelve un dict parcial."""
    r = dict(
        empresa='', domicilio='', cuit='', periodo='',
        empleado='', categoria='', legajo='', remuneracion='',
        fecha_ingreso='', cuil='', banco='',
        fecha_pago='', periodo_banco='',
        fecha_pago_aportes='', administracion='',
        haberes=[], descuentos=[], contrib=[],
        total_a_pagar=0.0, tot_descontar=0.0,
        total_contrib=0.0, tot_exentos=0.0, neto=0.0,
    )
    enc = 0

    for col in lineas:
        s = col.strip()
        if not s: continue

        # Empresa / domicilio
        if enc == 0 and 'NRO.ANSES' not in col:
            r['empresa'] = s; enc=1; continue
        if enc == 1 and 'NRO.ANSES' not in col and 'NRO.CUIT' not in col:
            r['domicilio'] = s; enc=2; continue

        # CUIT / período
        m = re.search(r'NRO\.CUIT\s*:\s*([\d/]+)\s+(\w+)\s+(\d{4})', col)
        if m:
            r['cuit'] = m.group(1); r['periodo'] = f"{m.group(2)} {m.group(3)}"; continue

        # Empleado / categoría
        if not r['empleado']:
            m = re.match(r'\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?)\s{3,}(Mensual|Quincenal|Jornalizado|Semanal)', col)
            if m: r['empleado']=m.group(1).strip(); r['categoria']=m.group(2).strip(); continue

        # Legajo, fecha ingreso, sector, remuneración
        # formato: ' 20004    03/12/03             003             962.300,00'
        if not r['legajo']:
            m = re.match(r'\s+(\d{4,})-?\s+(\d{2}/\d{2}/\d{2,4})\s+(\w+)\s+([\d.,]+)', col)
            if m:
                r['legajo']=m.group(1); r['fecha_ingreso']=m.group(2)
                r['remuneracion']=m.group(4); continue

        # Banco / fecha pago / período banco
        if not r['banco']:
            m = re.search(r'(GALICIA|SUPERVIELLE|FRANCES|NACION|PROVINCIA|SANTANDER|HSBC|ICBC|MACRO|BBVA|PATAGONIA|CIUDAD)', col, re.I)
            if m: r['banco']=m.group(1).upper()
            # fecha y periodo banco: '110526    0426   FRANCES'
            m2 = re.search(r'(\d{6})\s+(\d{4})', col)
            if m2: r['fecha_pago']=m2.group(1); r['periodo_banco']=m2.group(2)

        # ADMINISTRACION fecha pago aportes
        if 'ADMINISTRACION' in col.upper():
            m = re.search(r'(\d{8})', col)
            if m:
                d=m.group(1)
                r['fecha_pago_aportes']=f"{d[0:2]}/{d[2:4]}/{d[4:8]}"
            continue

        # CUIL + neto (última línea numérica del bloque)
        m = re.search(r'NRO\.CUIL\s*:\s*([\d/]+)\s+([\d.,]+)', col)
        if m:
            r['cuil']=m.group(1)
            r['neto']=pm(m.group(2)); continue

        # ── Línea "UN MILLON..." → captura total_a_pagar y tot_descontar ──────
        # Formato: '   UN MILLON...   1429036,00              '   (pos 44, texto a izq)
        #          '   TE PESOS.-                  243836,12   '   (pos 56, texto a izq)
        if re.search(r'(MILLON|MIL PESOS|PESOS\.)', col, re.I) and not re.match(r'\s{40,}', col):
            montos_lin = [(m2.start(), pm(m2.group()))
                          for m2 in re.finditer(r'\d{4,}[,.]\d{2}', col)
                          if pm(m2.group()) > 1000]
            for pos, monto in montos_lin:
                if pos >= 54:
                    if not r['tot_descontar']: r['tot_descontar'] = monto
                elif pos >= 43:
                    if not r['total_a_pagar']: r['total_a_pagar'] = monto
            continue

        # ── Líneas de totales: solo espacios antes de col 40 ─────────────────
        # col 44-53: total_contrib (326888 pág1 / 329037 pág2, o 358982 Alegre)
        # col 54+:   tot_descontar
        # NUNCA es total_a_pagar (ese viene siempre en línea con texto "UN MILLON")
        if re.match(r'\s{40,}', col) and not re.match(r'\s+\d{3}\s', col) \
                and not re.search(r'(OBRA SOCIAL|NRO\.CUIL)', col, re.I):
            montos = [(m2.start(), pm(m2.group()))
                      for m2 in re.finditer(r'\d{4,}[,.]\d{2}', col)
                      if pm(m2.group()) > 100]
            for pos, monto in montos:
                if pos >= 54:
                    if not r['tot_descontar']: r['tot_descontar'] = monto
                elif pos >= 43:
                    # Siempre es total_contrib (sobrescribir con el más reciente = pág2)
                    r['total_contrib'] = monto
            continue

        # Línea OBRA SOCIAL → tot_exentos
        m_os = re.search(r'OBRA SOCIAL:.*?(\d{4,}[,.]\d{2})', col)
        if m_os:
            if not r['tot_exentos']: r['tot_exentos'] = pm(m_os.group(1))
            continue


        # Conceptos con código de 3 dígitos
        m = re.match(r'\s+(\d{3})\s+(.*)', col)
        if not m: continue
        codigo = m.group(1)
        resto  = m.group(2)

        # Descripción: hasta primer doble espacio
        desc = re.split(r'\s{2,}', resto)[0].strip()[:35]

        # Porcentaje/cantidad: cols 21-33 (antes del monto grande)
        # Captura valores como '4,70', '10,17', ',94'
        cant_raw = col[21:33].strip()
        m_pct = re.search(r'[\d,]+', cant_raw)
        cant = m_pct.group() if m_pct else ''
        # Asegurar que tenga dos decimales y termine en %
        if cant and cant not in ('0',''):
            # agregar % solo si es un porcentaje (tiene coma o es <= 100)
            try:
                val = float(cant.replace(',','.'))
                cant = cant + '%' if val <= 100 else cant
            except: pass

        # Posiciones de montos verificadas empíricamente:
        # contrib empleador (800+): cols 32-45
        # haberes: cols 43-55
        # descuentos: cols 55-68
        nums = [(m2.start(), m2.end(), m2.group())
                for m2 in re.finditer(r'[\d.,]{4,}', col)
                if pm(m2.group()) > 0.5]

        if codigo in RUBRO:
            # Tomar el número más a la izquierda en zona contrib (cols 30-50)
            contrib_nums = [n for n in nums if 29 <= n[0] <= 50]
            if contrib_nums:
                monto = pm(contrib_nums[0][2])
                if monto > 1:
                    r['contrib'].append((codigo, desc, cant, monto))
        else:
            # Descuento: número en cols 54+
            desc_nums = [n for n in nums if n[0] >= 54]
            # Haber: número en cols 40-54
            hab_nums  = [n for n in nums if 39 <= n[0] < 54]

            if desc_nums:
                monto = pm(desc_nums[0][2])
                if monto > 0:
                    r['descuentos'].append((codigo, desc, cant, monto))
            elif hab_nums:
                monto = pm(hab_nums[0][2])
                if monto > 0:
                    r['haberes'].append((codigo, desc, cant, monto))

    return r

def parsear_txt(ruta):
    with open(ruta, 'r', encoding='latin-1', errors='replace') as f:
        contenido = f.read()

    bloques_raw = contenido.split('\x0c')
    parciales = []
    for bloque in bloques_raw:
        lineas = [l[:68] for l in bloque.splitlines()]
        p = parsear_bloque(lineas)
        if p['empresa'] or p['haberes'] or p['contrib']:
            parciales.append(p)

    # Fusionar bloques del mismo empleado (recibos de 2 páginas)
    recibos = []
    for p in parciales:
        key = (p['cuil'], p['periodo']) if p['cuil'] else None
        existente = None
        if key:
            existente = next((r for r in recibos
                              if (r['cuil'], r['periodo']) == key), None)
        if existente:
            # Agregar conceptos nuevos
            codigos_h = {x[0] for x in existente['haberes']}
            codigos_d = {x[0] for x in existente['descuentos']}
            codigos_c = {x[0] for x in existente['contrib']}
            for item in p['haberes']:
                if item[0] not in codigos_h: existente['haberes'].append(item)
            for item in p['descuentos']:
                if item[0] not in codigos_d: existente['descuentos'].append(item)
            for item in p['contrib']:
                if item[0] not in codigos_c: existente['contrib'].append(item)
            # Tomar totales del último bloque (más completo)
            if p['neto'] > 0: existente['neto'] = p['neto']
            # Fusión de totales: siempre tomar el valor del bloque más reciente si es > 0
            # total_a_pagar: viene de línea "UN MILLON" → tomar el mayor (más completo)
            if p['total_a_pagar'] > existente['total_a_pagar']:
                existente['total_a_pagar'] = p['total_a_pagar']
            # total_contrib: sobrescribir con el del bloque 2 (es el definitivo con ART/834)
            if p['total_contrib'] > 0:
                existente['total_contrib'] = p['total_contrib']
            if p['tot_descontar'] > 0:
                existente['tot_descontar'] = p['tot_descontar']
            if p['tot_exentos'] > existente['tot_exentos']:
                existente['tot_exentos'] = p['tot_exentos']
            for campo in ('legajo','fecha_ingreso','banco','fecha_pago_aportes',
                          'categoria','empleado','remuneracion','fecha_pago','periodo_banco'):
                if not existente.get(campo) and p.get(campo):
                    existente[campo] = p[campo]
        else:
            recibos.append(p)

    return recibos

# ── Agrupar rubros ───────────────────────────────────────────
def agrupar(contrib):
    rubros = {}
    for cod, _, _c, monto in contrib:
        rub = RUBRO.get(cod, 'Otros')
        rubros[rub] = rubros.get(rub, 0) + monto
    return rubros

# ── Gráfico de torta ─────────────────────────────────────────
def grafico_torta(rubros_contrib, neto):
    total_costo = sum(rubros_contrib.values()) + neto
    datos = dict(rubros_contrib)
    datos['Sueldo Neto'] = neto
    labels  = list(datos.keys())
    valores = list(datos.values())
    cols_g  = [COLORES.get(l, '#90A4AE') for l in labels]

    fig, ax = plt.subplots(figsize=(3.6, 3.0))
    wedges, _ = ax.pie(
        valores, colors=cols_g, startangle=90,
        wedgeprops={'edgecolor':'white','linewidth':0.8},
        counterclock=False,
    )
    for wedge, val in zip(wedges, valores):
        pct = val / total_costo * 100
        if pct >= 3:
            ang = (wedge.theta1 + wedge.theta2) / 2
            x_ = 0.68 * np.cos(np.radians(ang))
            y_ = 0.68 * np.sin(np.radians(ang))
            ax.text(x_, y_, f'{pct:.0f}%', ha='center', va='center',
                    fontsize=6.5, color='white', fontweight='bold')

    leyenda = [f"{l}  {v/total_costo*100:.0f}%" for l, v in zip(labels, valores)]
    ax.legend(wedges, leyenda, loc='lower center', bbox_to_anchor=(0.5,-0.30),
              ncol=2, fontsize=5.5, frameon=False, handlelength=1.0, columnspacing=0.8)
    plt.tight_layout(pad=0.1)
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    plt.savefig(tmp.name, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return tmp.name

# ── Helpers dibujo ───────────────────────────────────────────
AZUL      = colors.HexColor('#1565C0')
NARANJA   = colors.HexColor('#E65100')
VIOLETA   = colors.HexColor('#4A148C')
VERDE_OSC = colors.HexColor('#1B5E20')
GRIS_ENC  = colors.HexColor('#E3F2FD')
AZUL_CLA  = colors.HexColor('#BBDEFB')
NARANJA_C = colors.HexColor('#FBE9E7')
VIOLETA_C = colors.HexColor('#EDE7F6')
VERDE_CLA = colors.HexColor('#E8F5E9')
GRIS_ALT  = colors.HexColor('#F5F5F5')

def rf(c, x, y, w, h, col):
    c.setFillColor(col); c.rect(x,y,w,h,fill=1,stroke=0); c.setFillColor(colors.black)

def brd(c, x, y, w, h, col=colors.HexColor('#BDBDBD'), lw=0.2):
    c.setStrokeColor(col); c.setLineWidth(lw)
    c.rect(x,y,w,h,fill=0,stroke=1); c.setStrokeColor(colors.black)

def lh(c, x1, x2, y, col=colors.grey, lw=0.4):
    c.setStrokeColor(col); c.setLineWidth(lw); c.line(x1,y,x2,y); c.setStrokeColor(colors.black)

def celda_enc(c, x, y, w, h, txt, fs=6.2):
    rf(c,x,y,w,h,GRIS_ENC); brd(c,x,y,w,h)
    c.setFont("Helvetica-Bold",fs); c.drawCentredString(x+w/2,y+h*0.28,txt)

def celda_val(c, x, y, w, h, txt, fs=7.5, align='center', bold=False):
    brd(c,x,y,w,h)
    c.setFont("Helvetica-Bold" if bold else "Helvetica", fs)
    txt = str(txt)
    if   align=='center': c.drawCentredString(x+w/2, y+h*0.28, txt)
    elif align=='right':  c.drawRightString(x+w-3,  y+h*0.28, txt)
    else:                 c.drawString(x+3,          y+h*0.28, txt)

def banda(c, x, y, w, h, txt, bg, monto='', fs=8):
    rf(c,x,y,w,h,bg)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold",fs)
    c.drawString(x+5, y+h*0.3, txt)
    if monto:
        c.setFont("Helvetica-Bold", fs+1)
        c.drawRightString(x+w-5, y+h*0.3, monto)
    c.setFillColor(colors.black)

# ── Dibujo del recibo ────────────────────────────────────────
def dibujar_recibo(c, r, W, H):
    ML=1.1*cm; MR=W-1.1*cm; AW=MR-ML
    ROW=0.36*cm; BH=0.44*cm
    y = H - 0.8*cm

    # Calcular totales desde el TXT
    # Sueldo bruto = TOTAL A PAGAR (remunerativos)
    bruto       = r['total_a_pagar'] if r['total_a_pagar'] > 0 else \
                  sum(m for _,_,_,m in r['haberes'] if _ not in CODIGOS_NO_REM)
    descuentos  = r['tot_descontar'] if r['tot_descontar'] > 0 else \
                  sum(m for _,_,_,m in r['descuentos'])
    contrib_tot = r['total_contrib'] if r['total_contrib'] > 0 else \
                  sum(m for _,_,_,m in r['contrib'])
    no_rem      = r['tot_exentos']   if r['tot_exentos']   > 0 else \
                  sum(m for cod,_,_,m in r['haberes'] if cod in CODIGOS_NO_REM)
    neto        = r['neto']
    costo_total = bruto + contrib_tot
    rubros      = agrupar(r['contrib'])

    # ══ CABECERA EMPRESA ══════════════════════════════════════
    c.setFont("Helvetica-Bold",9); c.drawString(ML,y,r['empresa']); y-=0.34*cm
    c.setFont("Helvetica",7.5);    c.drawString(ML,y,r['domicilio']); y-=0.28*cm
    c.setFont("Helvetica",7.5);    c.drawString(ML,y,f"C.U.I.T. EMPRESA: {r['cuit']}"); y-=0.30*cm

    # ══ TABLA CABECERA ════════════════════════════════════════
    HF=0.38*cm; HV=0.40*cm
    mes, anio = (r['periodo'].split()+[''])[:2]
    ant = antiguedad(r['fecha_ingreso'])

    # Fila 1
    cw1=[AW*p for p in [0.05,0.10,0.08,0.35,0.13,0.18,0.11]]
    xs1=[ML]; [xs1.append(xs1[-1]+w) for w in cw1[:-1]]
    for t,x,w in zip(['Q.','MES','AÑO','APELLIDO Y NOMBRE','N°LEGAJO','SUELDO BRUTO','ANTIGÜEDAD'],xs1,cw1):
        celda_enc(c,x,y-HF,w,HF,t)
    y-=HF
    for v,x,w,al in zip(['',mes,anio,r['empleado'],r['legajo'],'$'+r['remuneracion'],ant],
                         xs1,cw1,['c','c','c','l','c','r','c']):
        celda_val(c,x,y-HV,w,HV,v,align=al)
    y-=HV

    # Fila 2
    cw2=[AW*p for p in [0.16,0.35,0.20,0.18,0.11]]
    xs2=[ML]; [xs2.append(xs2[-1]+w) for w in cw2[:-1]]
    fp=r.get('fecha_pago_aportes','') or 'Per. ant.'
    for t,x,w in zip(['FECHA INGRESO','CATEGORÍA LABORAL','C.U.I.L.','LUGAR DE PAGO','F.PAGO APORTES'],xs2,cw2):
        celda_enc(c,x,y-HF,w,HF,t)
    y-=HF
    for v,x,w in zip([r['fecha_ingreso'],r['categoria'],r['cuil'],r['banco'] or '—',fp],xs2,cw2):
        celda_val(c,x,y-HV,w,HV,v)
    y-=HV+0.08*cm

    # ══ BANDA: COSTO TOTAL EMPLEADOR ══════════════════════════
    banda(c,ML,y-BH,AW,BH,"COSTO TOTAL EMPLEADOR",AZUL,fm(costo_total))
    y-=BH

    # ══ SECCIÓN B: tabla contrib (izq) + gráfico (der abajo) ══
    # Layout según Anexo III: tabla izquierda, gráfico derecha-abajo
    TW=AW*0.56; GW=AW-TW-0.2*cm
    y_sec_b = y   # tope sección B

    # Encabezado tabla contrib
    cw_b=[TW*p for p in [0.44,0.18,0.20,0.18]]
    xs_b=[ML]; [xs_b.append(xs_b[-1]+w) for w in cw_b[:-1]]
    for t,x,w in zip(['CONCEPTO','UNIDAD','BASE','MONTO'],xs_b,cw_b):
        celda_enc(c,x,y-ROW,w,ROW,t)
    y-=ROW

    alt=True
    for cod,desc,cant,monto in r['contrib']:
        bg=GRIS_ALT if alt else colors.white
        rf(c,ML,y-ROW+2,TW,ROW,bg); brd(c,ML,y-ROW+2,TW,ROW,lw=0.12)
        c.setFont("Helvetica",7)
        c.drawString(ML+3, y-ROW+5, f"{cod} {desc}")
        # Unidad = porcentaje/cantidad si existe
        if cant:
            c.drawCentredString(xs_b[1]+cw_b[1]/2, y-ROW+5, cant+'%' if ',' in cant else cant)
        c.drawRightString(ML+TW-3, y-ROW+5, fm(monto))
        y-=ROW; alt=not alt

    lh(c,ML,ML+TW,y+2,col=AZUL,lw=0.7)
    rf(c,ML,y-ROW+2,TW,ROW,AZUL_CLA); brd(c,ML,y-ROW+2,TW,ROW,lw=0.12)
    c.setFont("Helvetica-Bold",7.5)
    c.drawString(ML+3,y-ROW+5,"SUB TOTAL CONTRIBUCIONES EMPLEADOR")
    c.drawRightString(ML+TW-3,y-ROW+5,fm(contrib_tot))
    y-=ROW

    rf(c,ML,y-ROW+2,TW,ROW,AZUL); brd(c,ML,y-ROW+2,TW,ROW,lw=0.12)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold",8)
    c.drawString(ML+3,y-ROW+5,"SUELDO BRUTO")
    c.drawRightString(ML+TW-3,y-ROW+5,fm(bruto))
    c.setFillColor(colors.black)
    y-=ROW

    y_fin_b = y  # fin tabla B

    # Gráfico: derecha de sección B, SIEMPRE dentro de los límites
    # y_sec_b = tope superior, y_fin_b = fondo. El gráfico ocupa ese espacio exacto.
    GH_disponible = y_sec_b - y_fin_b          # altura real de sección B
    GH = min(GH_disponible, 5.8*cm)            # no más de 5.8cm
    gx = ML + TW + 0.2*cm
    # Anclado al FONDO para que nunca suba más allá de y_sec_b
    gy = y_fin_b
    png = grafico_torta(rubros, neto)
    c.drawImage(png, gx, gy, width=GW, height=GH,
                preserveAspectRatio=True, anchor='sw')
    os.unlink(png)
    c.setFont("Helvetica", 5.0); c.setFillColor(colors.grey)
    c.drawString(gx, gy - 0.16*cm, "Nota: Seg.Social incluye SIPA, Fondo Emp. y Asig.Fam.")
    c.setFillColor(colors.black)

    y=y_fin_b-0.22*cm
    lh(c,ML,MR,y,col=AZUL,lw=0.8); y-=0.10*cm

    # ══ BANDA: SUELDO BRUTO (inicio sección C) ════════════════
    banda(c,ML,y-BH,AW,BH,"SUELDO BRUTO",NARANJA,fm(bruto))
    y-=BH

    # Encabezado conceptos
    CC=[ML,ML+0.85*cm,ML+AW*0.48,MR-3.8*cm,MR]
    rf(c,ML,y-ROW,AW,ROW,NARANJA_C); brd(c,ML,y-ROW,AW,ROW,lw=0.12)
    c.setFont("Helvetica-Bold",6.8)
    c.drawString(CC[0]+2,y-ROW+4,"CÓD")
    c.drawString(CC[1]+2,y-ROW+4,"DESCRIPCIÓN")
    c.drawRightString(CC[2],y-ROW+4,"CANT/UNIDAD")
    c.drawRightString(CC[3],y-ROW+4,"HABERES")
    c.drawRightString(CC[4],y-ROW+4,"DEDUCCIONES")
    y-=ROW

    hab_d  = {cod:(desc,cant,m) for cod,desc,cant,m in r['haberes']}
    desc_d = {cod:(desc,cant,m) for cod,desc,cant,m in r['descuentos']}
    orden  = list(dict.fromkeys([x[0] for x in r['haberes']]+[x[0] for x in r['descuentos']]))

    alt=True
    for cod in orden:
        if y<5.0*cm: break
        h=hab_d.get(cod); d=desc_d.get(cod)
        lbl=(h or d)[0]; cant_v=(h or d)[1]
        bg=colors.HexColor('#FFF3E0') if alt else colors.white
        rf(c,ML,y-ROW+2,AW,ROW,bg); brd(c,ML,y-ROW+2,AW,ROW,lw=0.12)
        c.setFont("Helvetica",7.5)
        c.drawString(CC[0]+2,y-ROW+5,cod)
        c.drawString(CC[1]+2,y-ROW+5,lbl)
        if cant_v:
            c.drawRightString(CC[2],y-ROW+5,cant_v)
        if h: c.drawRightString(CC[3],y-ROW+5,fm(h[2]))
        if d: c.drawRightString(CC[4],y-ROW+5,fm(d[2]))
        y-=ROW; alt=not alt

    lh(c,ML,MR,y+2,col=NARANJA,lw=0.6); y-=0.04*cm
    rf(c,ML,y-ROW,AW,ROW,NARANJA_C); brd(c,ML,y-ROW,AW,ROW,lw=0.12)
    c.setFont("Helvetica-Bold",7.5)
    c.drawString(CC[1]+2,y-ROW+5,"COMPOSICIÓN SALARIAL")
    c.drawString(CC[1]+2+3.5*cm,y-ROW+5,f"Rem: {fm(bruto)}  No Rem: {fm(no_rem)}  Desc: {fm(descuentos)}")
    y-=ROW+0.08*cm
    lh(c,ML,MR,y,col=NARANJA,lw=0.8); y-=0.10*cm

    # ══ SECCIÓN D: SUELDO NETO ════════════════════════════════
    banda(c,ML,y-BH,AW,BH,"SUELDO NETO $",VIOLETA)
    y-=BH
    NH=0.78*cm
    rf(c,ML,y-NH,AW,NH,VIOLETA_C); brd(c,ML,y-NH,AW,NH,lw=0.3)
    c.setFont("Helvetica-Bold",12); c.drawString(ML+0.4*cm,y-NH+NH*0.25,"NETO A PERCIBIR")
    c.setFont("Helvetica-Bold",14); c.drawRightString(MR-0.4*cm,y-NH+NH*0.25,fm(neto))
    y-=NH+0.10*cm

    if r['banco']:
        c.setFont("Helvetica",7); c.drawString(ML,y,f"Acreditado en: Banco {r['banco']}"); y-=0.28*cm

    # ══ TABLA INFERIOR (detalle composición) ══════════════════
    y-=0.08*cm
    W2=AW/2-0.15*cm; x2=ML+W2+0.3*cm; SR=0.29*cm

    ap_jub=sum(m for cod,_,_,m in r['descuentos'] if cod=='600')
    ap_ins=sum(m for cod,_,_,m in r['descuentos'] if cod=='602')
    ap_os =sum(m for cod,_,_,m in r['descuentos'] if cod in ('614','615'))

    def bloque_r(c,x,y,w,titulo,emp,trab):
        rf(c,x,y-SR,w,SR,GRIS_ENC); brd(c,x,y-SR,w,SR,lw=0.12)
        c.setFont("Helvetica-Bold",6.5)
        c.drawString(x+2,y-SR+SR*0.3,titulo)
        c.drawRightString(x+w-2,y-SR+SR*0.3,fm(emp+trab))
        y-=SR
        for lbl,val in [("Empleador",emp),("Trabajador",trab)]:
            brd(c,x,y-SR,w,SR,lw=0.08)
            c.setFont("Helvetica",6.5)
            c.drawString(x+6,y-SR+SR*0.3,lbl)
            c.drawRightString(x+w-2,y-SR+SR*0.3,fm(val))
            y-=SR
        return y

    y0=y; y1=y
    y0=bloque_r(c,ML,y0,W2,"Total Costo Sindical",   rubros.get('Sindical',0),   0)
    y0-=0.04*cm
    y0=bloque_r(c,ML,y0,W2,"Total Seguridad Social", rubros.get('Seguridad Social',0), ap_jub)
    y0-=0.04*cm
    y0=bloque_r(c,ML,y0,W2,"Total Obra Social",      rubros.get('Obra Social',0), ap_os)

    y1=bloque_r(c,x2,y1,W2,"Total costo INSSJP",     rubros.get('INSSJP',0),     ap_ins)
    y1-=0.04*cm
    y1=bloque_r(c,x2,y1,W2,"Total costo ART",        rubros.get('ART',0),         0)
    y1-=0.04*cm
    y1=bloque_r(c,x2,y1,W2,"Total Costo SCVO/Otros", rubros.get('Otros',0),        0)

    # ══ PIE ═══════════════════════════════════════════════════
    lh(c,ML,MR,0.88*cm,col=colors.grey,lw=0.3)
    c.setFont("Helvetica",6); c.setFillColor(colors.grey)
    c.drawString(ML,0.62*cm,"Recibo emitido conforme Ley 27.802 — Decreto 407/2026 — Anexo III")
    c.drawRightString(MR,0.62*cm,"Original — Empleador")
    c.setFillColor(colors.black)

# ── Generar PDF ──────────────────────────────────────────────
def generar_pdf(recibos, salida):
    c=rl_canvas.Canvas(salida,pagesize=A4); W,H=A4
    for i,r in enumerate(recibos):
        dibujar_recibo(c,r,W,H); c.showPage()
        print(f"  ✓ {i+1}/{len(recibos)}: {r['empleado']} | {r['periodo']}")
    c.save()
    print(f"\n✅ PDF: {salida}  ({len(recibos)} recibo/s)")

if __name__=='__main__':
    if len(sys.argv)<3:
        print("Uso: python recibos_pdf.py entrada.txt salida.pdf"); sys.exit(1)
    recibos=parsear_txt(sys.argv[1])
    print(f"📋 Recibos: {len(recibos)}")
    if not recibos: sys.exit(1)
    generar_pdf(recibos,sys.argv[2])
