#!/usr/bin/env python3
"""
app.py — Conversor Recibos Decreto 407/2026
"""
from flask import Flask, request, send_file, render_template_string
import tempfile, os, sys, importlib

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB máximo

HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Recibos 407/2026 — Conversor</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #F0F4F8; color: #1a1a2e; min-height: 100vh; }

  header {
    background: linear-gradient(135deg, #1565C0 0%, #0D47A1 100%);
    color: white; padding: 20px 40px;
    display: flex; align-items: center; justify-content: space-between;
    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
  }
  .header-left h1 { font-size: 1.3rem; font-weight: 700; letter-spacing: -0.3px; }
  .header-left p  { font-size: 0.78rem; opacity: 0.8; margin-top: 3px; }
  .badge {
    background: #FFD600; color: #000; font-size: 0.72rem;
    font-weight: 700; padding: 5px 12px; border-radius: 20px;
  }

  main { max-width: 640px; margin: 48px auto; padding: 0 20px 60px; }

  .card {
    background: white; border-radius: 16px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.08); overflow: hidden;
  }
  .card-header {
    background: #1565C0; color: white;
    padding: 20px 28px;
  }
  .card-header h2 { font-size: 1.05rem; font-weight: 600; }
  .card-header p  { font-size: 0.82rem; opacity: 0.85; margin-top: 4px; }
  .card-body { padding: 28px; }

  .drop-zone {
    border: 2.5px dashed #90CAF9; border-radius: 12px;
    padding: 44px 20px; text-align: center; cursor: pointer;
    transition: all .2s; background: #F8FBFF; position: relative;
  }
  .drop-zone:hover, .drop-zone.over { border-color: #1565C0; background: #E3F2FD; }
  .drop-zone input[type=file] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%;
  }
  .dz-icon { font-size: 3rem; margin-bottom: 10px; }
  .dz-main { font-size: 0.95rem; color: #1565C0; font-weight: 600; }
  .dz-sub  { font-size: 0.78rem; color: #9E9E9E; margin-top: 4px; }

  #fname {
    margin-top: 12px; min-height: 22px;
    font-size: 0.85rem; color: #2E7D32; font-weight: 600; text-align: center;
  }

  .btn {
    display: block; width: 100%; margin-top: 20px;
    background: #1565C0; color: white; border: none;
    padding: 15px; border-radius: 10px; font-size: 1rem;
    font-weight: 700; cursor: pointer; transition: all .2s;
    letter-spacing: 0.3px;
  }
  .btn:hover:not(:disabled) { background: #0D47A1; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(21,101,192,0.35); }
  .btn:disabled { background: #B0BEC5; cursor: not-allowed; transform: none; box-shadow: none; }

  #status {
    margin-top: 18px; padding: 14px 16px; border-radius: 10px;
    font-size: 0.88rem; display: none; line-height: 1.5;
  }
  .ok   { background: #E8F5E9; color: #1B5E20; border: 1px solid #A5D6A7; }
  .err  { background: #FFEBEE; color: #B71C1C; border: 1px solid #EF9A9A; }
  .load { background: #E3F2FD; color: #0D47A1; border: 1px solid #90CAF9; }

  .spinner {
    display: inline-block; width: 14px; height: 14px;
    border: 2px solid #90CAF9; border-top-color: #1565C0;
    border-radius: 50%; animation: spin .7s linear infinite;
    vertical-align: middle; margin-right: 6px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .steps {
    background: #FFF8E1; border: 1px solid #FFE082;
    border-radius: 10px; padding: 18px 20px; margin-top: 22px;
  }
  .steps h3 { font-size: 0.85rem; color: #E65100; font-weight: 700; margin-bottom: 10px; }
  .steps ol  { padding-left: 18px; }
  .steps li  { font-size: 0.82rem; color: #5D4037; margin-bottom: 6px; line-height: 1.5; }
  .steps li strong { color: #BF360C; }

  footer {
    text-align: center; margin-top: 28px;
    font-size: 0.73rem; color: #9E9E9E; line-height: 1.6;
  }
</style>
</head>
<body>

<header>
  <div class="header-left">
    <h1>📄 Conversor de Recibos de Haberes</h1>
    <p>Ley 27.802 — Decreto 407/2026 — Anexo III</p>
  </div>
  <span class="badge">NEXSAT S.A.</span>
</header>

<main>
  <div class="card">
    <div class="card-header">
      <h2>Convertir TXT → PDF con gráfico de torta</h2>
      <p>Procesá todos los recibos del mes en un solo paso</p>
    </div>
    <div class="card-body">

      <form id="frm" enctype="multipart/form-data">
        <div class="drop-zone" id="dz">
          <input type="file" id="fi" name="txt_file" accept=".txt,.TXT">
          <div class="dz-icon">📂</div>
          <div class="dz-main">Hacé clic o arrastrá el archivo TXT acá</div>
          <div class="dz-sub">Archivos .TXT generados por el sistema COBOL</div>
        </div>
        <div id="fname"></div>
        <button class="btn" id="btn" type="submit" disabled>⚡ Generar PDF</button>
      </form>

      <div id="status"></div>

      <div class="steps">
        <h3>📋 ¿Cómo usar?</h3>
        <ol>
          <li>Seleccioná el TXT de la liquidación <strong>(puede contener todos los empleados)</strong></li>
          <li>Hacé clic en <strong>Generar PDF</strong></li>
          <li>Se descarga el PDF con <strong>un recibo por página</strong>, con el gráfico de torta incluido</li>
          <li>Subí el PDF a <strong>TuLegajo.com</strong> para su distribución por CUIL</li>
        </ol>
      </div>

    </div>
  </div>

  <footer>
    Recibos generados conforme Ley 27.802 — Decreto 407/2026 — Anexo III<br>
    Los archivos se procesan en el servidor y no se almacenan.
  </footer>
</main>

<script>
const fi  = document.getElementById('fi');
const btn = document.getElementById('btn');
const fn  = document.getElementById('fname');
const st  = document.getElementById('status');
const dz  = document.getElementById('dz');
const frm = document.getElementById('frm');

fi.addEventListener('change', () => {
  if (fi.files.length) {
    fn.textContent = '✅ ' + fi.files[0].name + '  (' + (fi.files[0].size/1024).toFixed(0) + ' KB)';
    btn.disabled = false;
  }
});

dz.addEventListener('dragover',  e => { e.preventDefault(); dz.classList.add('over'); });
dz.addEventListener('dragleave', () => dz.classList.remove('over'));
dz.addEventListener('drop', e => {
  e.preventDefault(); dz.classList.remove('over');
  fi.files = e.dataTransfer.files;
  fi.dispatchEvent(new Event('change'));
});

frm.addEventListener('submit', async e => {
  e.preventDefault();
  if (!fi.files.length) return;
  btn.disabled = true;
  st.className = 'load'; st.style.display = 'block';
  st.innerHTML = '<span class="spinner"></span> Procesando recibos, aguardá...';

  try {
    const fd = new FormData();
    fd.append('txt_file', fi.files[0]);
    const res = await fetch('/convertir', { method: 'POST', body: fd });

    if (res.ok) {
      const blob  = await res.blob();
      const url   = URL.createObjectURL(blob);
      const a     = document.createElement('a');
      const nom   = fi.files[0].name.replace(/\.txt$/i,'') + '_407.pdf';
      a.href = url; a.download = nom; a.click();
      URL.revokeObjectURL(url);
      const n = res.headers.get('X-Recibos') || '?';
      st.className = 'ok';
      st.innerHTML = `✅ PDF generado con <strong>${n} recibo/s</strong>.<br>
        Revisalo y subilo a <strong>TuLegajo.com</strong>.`;
    } else {
      st.className = 'err';
      st.innerHTML = '❌ Error: ' + await res.text();
    }
  } catch(err) {
    st.className = 'err';
    st.innerHTML = '❌ Error de conexión: ' + err;
  }
  btn.disabled = false;
});
</script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/convertir', methods=['POST'])
def convertir():
    if 'txt_file' not in request.files:
        return 'No se recibió archivo', 400
    f = request.files['txt_file']
    if not f.filename:
        return 'Archivo vacío', 400

    tmp_txt = tempfile.NamedTemporaryFile(suffix='.txt', delete=False)
    f.save(tmp_txt.name); tmp_txt.close()

    tmp_pdf = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    tmp_pdf.close()

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import recibos_pdf; importlib.reload(recibos_pdf)

        recibos = recibos_pdf.parsear_txt(tmp_txt.name)
        if not recibos:
            return 'No se encontraron recibos en el archivo', 400

        recibos_pdf.generar_pdf(recibos, tmp_pdf.name)

        nombre = f.filename.replace('.TXT','').replace('.txt','') + '_407.pdf'
        response = send_file(tmp_pdf.name, as_attachment=True,
                             download_name=nombre, mimetype='application/pdf')
        response.headers['X-Recibos'] = str(len(recibos))
        return response

    except Exception as ex:
        return f'Error al procesar: {str(ex)}', 500
    finally:
        try: os.unlink(tmp_txt.name)
        except: pass

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
