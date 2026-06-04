#!/usr/bin/env python3
"""
recibos_pdf.py — Decreto 407/2026 / Ley 27.802
Convierte TXT COBOL al formato oficial del Anexo III.
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
    '816': 'ART',
    '817': 'ART',
    '834': 'Otros',
}
# Agregar códigos sindicales / cámaras si corresponde:
# '8XX': 'Sindical'   '8XX': 'Cámaras'

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
    t = re.sub(r'[^\d,]','',txt.strip()).replace(',','.')
    try: return float(t)
    except: return 0.0

def fm(v):
    if v == 0: return '$0,00'
    p = f"{abs(v):,.2f}".split('.')
    return '$' + p[0].replace(',','.') + ',' + p[1]

def antiguedad(fi):
    if not fi: return '—'
    try:
        parts = fi.split('/')
        a = int(parts[2]); a = a+2000 if a<50 else (a+1900 if a<100 else a)
        from datetime import date
        return f"{date.today().year - a} año/s"
    except: return '—'

# ── Parser ───────────────────────────────────────────────────
def parsear_monto_concepto(col):
    """
    Layout real del TXT COBOL (verificado por posición de caracteres):
     cols 33-53: monto costo empleador (800-834) y también haberes
     cols 43-55: monto haberes
     cols 55-68: monto descuentos empleado
    Estrategia: buscar todos los números >= 4 dígitos en la línea
    y asignar según posición.
    """
    import re
    nums = [(m.start(), m.end(), m.group()) for m in re.finditer(r'[\d.,]{4,}', col)]
    # Filtrar el porcentaje/alícuota (suele ser < col 32 y < 6 chars)
    zona_contrib = ''
    zona_hab     = ''
    zona_desc    = ''
    for start, end, val in nums:
        if start >= 55:
            zona_desc = val
        elif start >= 42:
            zona_hab = val
        elif start >= 32:
            zona_contrib = val
    return zona_contrib, zona_hab, zona_desc

def es_monto(txt):
    return bool(txt) and bool(re.search(r'\d{2,}', txt)) and not re.match(r'^[\d,]{1,5}$', txt)

def extraer_monto(txt):
    m = re.search(r'[\d.,]+', txt)
    return pm(m.group()) if m else 0.0

def parsear_txt(ruta):
    with open(ruta, 'r', encoding='latin-1', errors='replace') as f:
        contenido = f.read()

    bloques = contenido.split('\x0c')
    recibos = []

    for bloque in bloques:
        lineas = bloque.splitlines()
        # Tomar columna izquierda
        lineas = [l[:68] for l in lineas]

        r = dict(
            empresa='', domicilio='', cuit='', periodo='',
            empleado='', categoria='', legajo='',
            fecha_ingreso='', cuil='', banco='',
            fecha_pago_aportes='', lugar_pago='',
            haberes=[], descuentos=[], contrib=[],
            neto=0.0, total_haberes=0.0, total_descuentos=0.0,
        )

        enc = 0  # estado encabezado

        for col in lineas:
            s = col.strip()
            if not s: continue

            # Empresa / domicilio
            if enc == 0 and 'NRO.ANSES' not in col:
                r['empresa'] = s; enc = 1; continue
            if enc == 1 and 'NRO.ANSES' not in col and 'NRO.CUIT' not in col:
                r['domicilio'] = s; enc = 2; continue

            # CUIT / período
            m = re.search(r'NRO\.CUIT\s*:\s*([\d/]+)\s+(\w+)\s+(\d{4})', col)
            if m:
                r['cuit'] = m.group(1)
                r['periodo'] = f"{m.group(2)} {m.group(3)}"
                continue

            # Empleado / categoría
            if not r['empleado']:
                m = re.match(r'\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?)\s{3,}(Mensual|Quincenal|Jornalizado|Semanal)', col)
                if m:
                    r['empleado']  = m.group(1).strip()
                    r['categoria'] = m.group(2).strip()
                    continue

            # Legajo / fecha ingreso  →  ' 20004    03/12/03'
            if not r['legajo']:
                m = re.match(r'\s+(\d{4,})-?\s+(\d{2}/\d{2}/\d{2,4})', col)
                if m:
                    r['legajo'] = m.group(1)
                    r['fecha_ingreso'] = m.group(2)
                    continue

            # Banco  →  '  110426    0326   FRANCES'
            if not r['banco']:
                m = re.search(r'(GALICIA|SUPERVIELLE|FRANCES|NACION|PROVINCIA|SANTANDER|HSBC|ICBC|MACRO|BBVA|PATAGONIA|CIUDAD)', col, re.I)
                if m: r['banco'] = m.group(1).upper()

            # Fecha pago aportes  →  ' ADMINISTRACION   07052026'
            if 'ADMINISTRACION' in col.upper():
                m = re.search(r'(\d{8})', col)
                if m:
                    d = m.group(1)
                    r['fecha_pago_aportes'] = f"{d[0:2]}/{d[2:4]}/{d[4:8]}"
                continue

            # CUIL + neto
            m = re.search(r'NRO\.CUIL\s*:\s*([\d/]+)\s+([\d.,]+)', col)
            if m:
                r['cuil'] = m.group(1)
                r['neto'] = extraer_monto(m.group(2))
                continue

            # Conceptos con código de 3 dígitos
            m = re.match(r'\s+(\d{3})\s+(.*)', col)
            if not m: continue

            codigo = m.group(1)
            resto  = m.group(2)
            desc   = re.split(r'\s{2,}|\s+[\d,]+\s', resto)[0].strip()[:35]

            zc, zh, zd = parsear_monto_concepto(col)

            if codigo in RUBRO:
                # Costo empleador: monto en zona_contrib o zona_hab
                raw = zc if es_monto(zc) else (zh if es_monto(zh) else '')
                if raw:
                    monto = extraer_monto(raw)
                    if monto > 1:
                        r['contrib'].append((codigo, desc, monto))
            elif es_monto(zd):
                monto = extraer_monto(zd)
                if monto > 0:
                    r['descuentos'].append((codigo, desc, monto))
            elif es_monto(zh):
                monto = extraer_monto(zh)
                # ignorar ajuste redondeo y montos < 1
                if monto > 1:
                    r['haberes'].append((codigo, desc, monto))

        if r['haberes'] or r['descuentos'] or r['contrib']:
            r['total_haberes']    = sum(x[2] for x in r['haberes'])
            r['total_descuentos'] = sum(x[2] for x in r['descuentos'])
            recibos.append(r)

    return recibos

# ── Agrupar rubros ───────────────────────────────────────────
def agrupar(contrib):
    rubros = {}
    for cod, _, monto in contrib:
        rub = RUBRO.get(cod, 'Otros')
        rubros[rub] = rubros.get(rub, 0) + monto
    return rubros

# ── Gráfico de torta ─────────────────────────────────────────
def grafico_torta(rubros_contrib, neto):
    """
    Muestra TODOS los componentes del costo total empleador.
    Cada porción = su % sobre el costo total (contrib + neto trabajador).
    """
    total_costo = sum(rubros_contrib.values()) + neto

    # Orden: primero rubros contrib, último sueldo neto
    datos = dict(rubros_contrib)
    datos['Sueldo Neto'] = neto

    labels  = list(datos.keys())
    valores = list(datos.values())
    cols_g  = [COLORES.get(l, '#90A4AE') for l in labels]

    fig, ax = plt.subplots(figsize=(4.0, 3.5))
    wedges, _ = ax.pie(
        valores,
        colors=cols_g,
        startangle=90,
        wedgeprops={'edgecolor': 'white', 'linewidth': 0.8},
        counterclock=False,
    )

    # Anotar % calculado sobre costo total
    for wedge, val in zip(wedges, valores):
        pct = val / total_costo * 100
        if pct >= 3:
            ang = (wedge.theta1 + wedge.theta2) / 2
            x_  = 0.68 * np.cos(np.radians(ang))
            y_  = 0.68 * np.sin(np.radians(ang))
            ax.text(x_, y_, f'{pct:.0f}%',
                    ha='center', va='center',
                    fontsize=6.5, color='white', fontweight='bold')

    # Leyenda con porcentaje real
    leyenda = [f"{l}  {v/total_costo*100:.0f}%" for l, v in zip(labels, valores)]
    ax.legend(wedges, leyenda,
              loc='lower center', bbox_to_anchor=(0.5, -0.28),
              ncol=2, fontsize=5.8, frameon=False, handlelength=1.0,
              columnspacing=0.8)

    plt.tight_layout(pad=0.2)
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    plt.savefig(tmp.name, dpi=150, bbox_inches='tight',
                facecolor='white', transparent=False)
    plt.close(fig)
    return tmp.name

# ── Helpers dibujo ───────────────────────────────────────────
GRIS_ENC  = colors.HexColor('#E3F2FD')
AZUL      = colors.HexColor('#1565C0')
NARANJA   = colors.HexColor('#E65100')
VIOLETA   = colors.HexColor('#4A148C')
VERDE_OSC = colors.HexColor('#1B5E20')
VERDE_CLA = colors.HexColor('#E8F5E9')
NARANJA_C = colors.HexColor('#FBE9E7')
VIOLETA_C = colors.HexColor('#EDE7F6')

def rect_fill(c, x, y, w, h, color):
    c.setFillColor(color); c.rect(x, y, w, h, fill=1, stroke=0); c.setFillColor(colors.black)

def borde(c, x, y, w, h, color=colors.HexColor('#BDBDBD'), lw=0.25):
    c.setStrokeColor(color); c.setLineWidth(lw)
    c.rect(x, y, w, h, fill=0, stroke=1); c.setStrokeColor(colors.black)

def linea_h(c, x1, x2, y, col=colors.grey, lw=0.4):
    c.setStrokeColor(col); c.setLineWidth(lw); c.line(x1, y, x2, y); c.setStrokeColor(colors.black)

def celda_enc(c, x, y, w, h, txt, fs=6.5):
    rect_fill(c, x, y, w, h, GRIS_ENC)
    borde(c, x, y, w, h)
    c.setFont("Helvetica-Bold", fs)
    c.drawCentredString(x+w/2, y+h*0.28, txt)

def celda_val(c, x, y, w, h, txt, fs=7.5, align='center', bold=False):
    borde(c, x, y, w, h)
    c.setFont("Helvetica-Bold" if bold else "Helvetica", fs)
    if align == 'center': c.drawCentredString(x+w/2, y+h*0.28, str(txt))
    elif align == 'right': c.drawRightString(x+w-3, y+h*0.28, str(txt))
    else: c.drawString(x+3, y+h*0.28, str(txt))

def titulo_banda(c, x, y, w, h, txt, bg, fg=colors.white, fs=8):
    rect_fill(c, x, y, w, h, bg)
    c.setFillColor(fg); c.setFont("Helvetica-Bold", fs)
    c.drawString(x+4, y+h*0.3, txt); c.setFillColor(colors.black)

# ── Dibujo del recibo ────────────────────────────────────────
def dibujar_recibo(c, r, W, H):
    ML = 1.1*cm; MR = W - 1.1*cm; AW = MR - ML
    ROW = 0.38*cm   # alto de fila estándar
    y = H - 0.9*cm  # posición Y actual (baja)

    bruto  = r['total_haberes']
    rubros = agrupar(r['contrib'])
    total_contrib = sum(x[2] for x in r['contrib'])
    costo_total   = bruto + total_contrib

    # ═══════════════════════════════════════════════════════
    # CABECERA EMPRESA
    # ═══════════════════════════════════════════════════════
    c.setFont("Helvetica-Bold", 9); c.drawString(ML, y, r['empresa']); y -= 0.35*cm
    c.setFont("Helvetica", 7.5);    c.drawString(ML, y, r['domicilio']); y -= 0.30*cm
    c.setFont("Helvetica", 7.5);    c.drawString(ML, y, f"C.U.I.T. EMPRESA: {r['cuit']}"); y -= 0.32*cm

    # ═══════════════════════════════════════════════════════
    # TABLA CABECERA — fila 1 y fila 2 (modelo Anexo III)
    # ═══════════════════════════════════════════════════════
    HF = 0.40*cm  # alto fila encabezado
    HV = 0.42*cm  # alto fila valor

    # Fila 1: Q | MES | AÑO | APELLIDO Y NOMBRE | N°LEGAJO | SUELDO BRUTO | ANTIGÜEDAD
    mes, anio = (r['periodo'].split()+[''])[:2]
    ant = antiguedad(r['fecha_ingreso'])
    cw1 = [AW*p for p in [0.05, 0.10, 0.08, 0.35, 0.13, 0.18, 0.11]]
    xs1 = [ML]; [xs1.append(xs1[-1]+w) for w in cw1[:-1]]

    for enc_txt, x, w in zip(['Q.','MES','AÑO','APELLIDO Y NOMBRE','N°LEGAJO','SUELDO BRUTO','ANTIGÜEDAD'], xs1, cw1):
        celda_enc(c, x, y-HF, w, HF, enc_txt)
    y -= HF
    for val, x, w, al in zip(['', mes, anio, r['empleado'], r['legajo'], fm(bruto), ant],
                               xs1, cw1, ['c','c','c','l','c','r','c']):
        celda_val(c, x, y-HV, w, HV, val, align=al)
    y -= HV

    # Fila 2: FECHA INGRESO | CATEGORÍA LABORAL | C.U.I.L. | LUGAR DE PAGO | F.PAGO APORTES
    cw2 = [AW*p for p in [0.16, 0.35, 0.20, 0.18, 0.11]]
    xs2 = [ML]; [xs2.append(xs2[-1]+w) for w in cw2[:-1]]
    for enc_txt, x, w in zip(['FECHA INGRESO','CATEGORÍA LABORAL','C.U.I.L.','LUGAR DE PAGO','F.PAGO APORTES'], xs2, cw2):
        celda_enc(c, x, y-HF, w, HF, enc_txt)
    y -= HF
    fp = r['fecha_pago_aportes'] or 'Per. anterior'
    for val, x, w in zip([r['fecha_ingreso'], r['categoria'], r['cuil'], r['banco'] or '—', fp], xs2, cw2):
        celda_val(c, x, y-HV, w, HV, val)
    y -= HV + 0.10*cm

    # ═══════════════════════════════════════════════════════
    # BANDA: COSTO TOTAL EMPLEADOR
    # ═══════════════════════════════════════════════════════
    BH = 0.44*cm
    titulo_banda(c, ML, y-BH, AW, BH,
        f"COSTO TOTAL EMPLEADOR", AZUL)
    # Monto alineado a la derecha en la misma banda
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 9)
    c.drawRightString(MR-3, y-BH+BH*0.3, fm(costo_total))
    c.setFillColor(colors.black)
    y -= BH

    # ═══════════════════════════════════════════════════════
    # SECCIÓN B: tabla contrib (izq) + gráfico (der)
    # ═══════════════════════════════════════════════════════
    TW = AW * 0.55   # ancho tabla contribuciones
    GW = AW - TW - 0.2*cm  # ancho gráfico

    y_tab = y  # guardamos Y de inicio de sección B

    # Encabezado tabla contrib
    cw_b = [TW*p for p in [0.42, 0.20, 0.20, 0.18]]
    xs_b = [ML]; [xs_b.append(xs_b[-1]+w) for w in cw_b[:-1]]
    for enc_txt, x, w in zip(['CONCEPTO','UNIDAD','BASE','MONTO'], xs_b, cw_b):
        celda_enc(c, x, y-ROW, w, ROW, enc_txt)
    y -= ROW

    alt = True
    for cod, desc, monto in r['contrib']:
        bg = colors.HexColor('#F5F5F5') if alt else colors.white
        rect_fill(c, ML, y-ROW+2, TW, ROW, bg)
        borde(c, ML, y-ROW+2, TW, ROW, lw=0.15)
        c.setFont("Helvetica", 7)
        c.drawString(ML+3, y-ROW+6, f"{cod} {desc}")
        c.drawRightString(ML+TW-3, y-ROW+6, fm(monto))
        y -= ROW; alt = not alt

    # Línea separadora subtotal
    linea_h(c, ML, ML+TW, y+2, col=AZUL, lw=0.7)

    # Sub total contribuciones
    rect_fill(c, ML, y-ROW+2, TW, ROW, colors.HexColor('#BBDEFB'))
    borde(c, ML, y-ROW+2, TW, ROW, lw=0.15)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(ML+3, y-ROW+6, "SUB TOTAL CONTRIBUCIONES EMPLEADOR")
    c.drawRightString(ML+TW-3, y-ROW+6, fm(total_contrib))
    y -= ROW

    # Sueldo bruto
    rect_fill(c, ML, y-ROW+2, TW, ROW, AZUL)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 8)
    c.drawString(ML+3, y-ROW+6, "SUELDO BRUTO")
    c.drawRightString(ML+TW-3, y-ROW+6, fm(bruto))
    c.setFillColor(colors.black)
    y -= ROW

    y_fin_tab = y  # fin de tabla B

    # Gráfico de torta a la derecha, alineado con el inicio de sección B
    gh = y_tab - y_fin_tab
    gx = ML + TW + 0.2*cm
    png = grafico_torta(rubros, r['neto'])
    c.drawImage(png, gx, y_fin_tab, width=GW, height=max(gh, 5.0*cm),
                preserveAspectRatio=True, anchor='sw')
    os.unlink(png)

    # Nota al pie del gráfico
    c.setFont("Helvetica", 5.5); c.setFillColor(colors.grey)
    c.drawString(gx, y_fin_tab - 0.22*cm,
        "Nota: Seg. Social incluye SIPA, Fondo Nac. de Empleo y Asig. Familiares")
    c.setFillColor(colors.black)

    y = y_fin_tab - 0.28*cm
    linea_h(c, ML, MR, y, col=AZUL, lw=0.8)
    y -= 0.12*cm

    # ═══════════════════════════════════════════════════════
    # BANDA: SUELDO BRUTO (inicio sección C)
    # ═══════════════════════════════════════════════════════
    titulo_banda(c, ML, y-BH, AW, BH, "SUELDO BRUTO", NARANJA)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 9)
    c.drawRightString(MR-3, y-BH+BH*0.3, fm(bruto))
    c.setFillColor(colors.black)
    y -= BH

    # Encabezado conceptos sección C
    CC = [ML, ML+0.9*cm, ML+AW*0.50, MR-3.8*cm, MR]
    rect_fill(c, ML, y-ROW, AW, ROW, NARANJA_C)
    borde(c, ML, y-ROW, AW, ROW, lw=0.15)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(CC[0]+2, y-ROW+5, "CÓD")
    c.drawString(CC[1]+2, y-ROW+5, "CONCEPTO")
    c.drawRightString(CC[2],      y-ROW+5, "UNIDAD/BASE")
    c.drawRightString(CC[3],      y-ROW+5, "HABERES")
    c.drawRightString(CC[4],      y-ROW+5, "DEDUCCIONES")
    y -= ROW

    hab_d  = {cod: (desc, m) for cod, desc, m in r['haberes']}
    desc_d = {cod: (desc, m) for cod, desc, m in r['descuentos']}
    orden  = list(dict.fromkeys([x[0] for x in r['haberes']] + [x[0] for x in r['descuentos']]))

    alt = True
    for cod in orden:
        if y < 5.5*cm: break
        h = hab_d.get(cod); d = desc_d.get(cod)
        lbl = (h or d)[0]
        bg = colors.HexColor('#FFF3E0') if alt else colors.white
        rect_fill(c, ML, y-ROW+2, AW, ROW, bg)
        borde(c, ML, y-ROW+2, AW, ROW, lw=0.15)
        c.setFont("Helvetica", 7.5)
        c.drawString(CC[0]+2, y-ROW+5, cod)
        c.drawString(CC[1]+2, y-ROW+5, lbl)
        if h: c.drawRightString(CC[3], y-ROW+5, fm(h[1]))
        if d: c.drawRightString(CC[4], y-ROW+5, fm(d[1]))
        y -= ROW; alt = not alt

    # Composición salarial
    linea_h(c, ML, MR, y+2, col=NARANJA, lw=0.6)
    y -= 0.05*cm
    rem   = sum(m for cod, _, m in r['haberes'] if cod not in ('533','534','535'))
    norem = sum(m for cod, _, m in r['haberes'] if cod in ('533','534','535'))
    rect_fill(c, ML, y-ROW, AW, ROW, NARANJA_C)
    borde(c, ML, y-ROW, AW, ROW, lw=0.15)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(ML+3, y-ROW+5,
        f"COMPOSICIÓN SALARIAL:   Remunerativo: {fm(rem)}   "
        f"No Remunerativo: {fm(norem)}   Descuentos: {fm(r['total_descuentos'])}")
    y -= ROW + 0.10*cm
    linea_h(c, ML, MR, y, col=NARANJA, lw=0.8)
    y -= 0.12*cm

    # ═══════════════════════════════════════════════════════
    # SECCIÓN D: SUELDO NETO
    # ═══════════════════════════════════════════════════════
    titulo_banda(c, ML, y-BH, AW, BH, "SUELDO NETO $", VIOLETA)
    y -= BH
    NH = 0.80*cm
    rect_fill(c, ML, y-NH, AW, NH, VIOLETA_C)
    borde(c, ML, y-NH, AW, NH, lw=0.3)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(ML+0.5*cm, y-NH+NH*0.25, "NETO A PERCIBIR")
    c.setFont("Helvetica-Bold", 15)
    c.drawRightString(MR-0.5*cm, y-NH+NH*0.25, fm(r['neto']))
    y -= NH + 0.12*cm

    if r['banco']:
        c.setFont("Helvetica", 7.5)
        c.drawString(ML, y, f"Acreditado en: Banco {r['banco']}")
        y -= 0.32*cm

    # ═══════════════════════════════════════════════════════
    # TABLA INFERIOR — Detalle composición (modelo Anexo III)
    # ═══════════════════════════════════════════════════════
    y -= 0.10*cm
    W2 = AW/2 - 0.15*cm; x2 = ML + W2 + 0.3*cm
    SR = 0.30*cm  # alto fila tabla inferior

    def bloque_rubro(c, x, y, w, titulo, emp, trab):
        rect_fill(c, x, y-SR, w, SR, GRIS_ENC)
        borde(c, x, y-SR, w, SR, lw=0.15)
        c.setFont("Helvetica-Bold", 6.8)
        c.drawString(x+2, y-SR+SR*0.28, titulo)
        c.drawRightString(x+w-2, y-SR+SR*0.28, fm(emp+trab))
        y -= SR
        for lbl, val in [("Empleador", emp), ("Trabajador", trab)]:
            borde(c, x, y-SR, w, SR, lw=0.10)
            c.setFont("Helvetica", 6.5)
            c.drawString(x+6, y-SR+SR*0.28, lbl)
            c.drawRightString(x+w-2, y-SR+SR*0.28, fm(val))
            y -= SR
        return y

    # Aportes trabajador por código
    ap_jub = sum(m for cod,_,m in r['descuentos'] if cod == '600')
    ap_ins = sum(m for cod,_,m in r['descuentos'] if cod == '602')
    ap_os  = sum(m for cod,_,m in r['descuentos'] if cod in ('614','615'))

    y0 = y; y1 = y
    y0 = bloque_rubro(c, ML, y0, W2, "Total Costo Sindical",   rubros.get('Sindical',0),   0)
    y0 -= 0.06*cm
    y0 = bloque_rubro(c, ML, y0, W2, "Total Seguridad Social", rubros.get('Seguridad Social',0), ap_jub)
    y0 -= 0.06*cm
    y0 = bloque_rubro(c, ML, y0, W2, "Total Obra Social",      rubros.get('Obra Social',0), ap_os)

    y1 = bloque_rubro(c, x2, y1, W2, "Total costo INSSJP",     rubros.get('INSSJP',0),      ap_ins)
    y1 -= 0.06*cm
    y1 = bloque_rubro(c, x2, y1, W2, "Total costo ART",        rubros.get('ART',0),          0)
    y1 -= 0.06*cm
    y1 = bloque_rubro(c, x2, y1, W2, "Total Costo SCVO/Otros", rubros.get('Otros',0),         0)

    # ═══════════════════════════════════════════════════════
    # PIE DE PÁGINA
    # ═══════════════════════════════════════════════════════
    linea_h(c, ML, MR, 0.90*cm, col=colors.grey, lw=0.3)
    c.setFont("Helvetica", 6); c.setFillColor(colors.grey)
    c.drawString(ML, 0.65*cm,
        "Recibo emitido conforme Ley 27.802 — Decreto 407/2026 — Anexo III")
    c.drawRightString(MR, 0.65*cm, "Original — Empleador")
    c.setFillColor(colors.black)

# ── Generar PDF ──────────────────────────────────────────────
def generar_pdf(recibos, salida):
    c = rl_canvas.Canvas(salida, pagesize=A4)
    W, H = A4
    for i, r in enumerate(recibos):
        dibujar_recibo(c, r, W, H)
        c.showPage()
        print(f"  ✓ {i+1}/{len(recibos)}: {r['empleado']} | {r['periodo']}")
    c.save()
    print(f"\n✅ PDF: {salida}  ({len(recibos)} recibo/s)")

# ── Main ─────────────────────────────────────────────────────
if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Uso: python recibos_pdf.py entrada.txt salida.pdf"); sys.exit(1)
    recibos = parsear_txt(sys.argv[1])
    print(f"📋 Recibos encontrados: {len(recibos)}")
    if not recibos: print("⚠️  Sin recibos."); sys.exit(1)
    generar_pdf(recibos, sys.argv[2])
