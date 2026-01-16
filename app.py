from flask import Flask, render_template, request, redirect, url_for, send_file, session
import sqlite3, os, json, io
from datetime import datetime
from weasyprint import HTML
from werkzeug.utils import secure_filename
import json
from flask import request, jsonify

app = Flask(__name__)
app.secret_key = "gil_eletronicos_secret_2025"
DB_NAME = "database.db"
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def gerar_payload_pix(chave, nome, cidade, valor):
    """Gera o payload Pix estático no padrão EMV."""
    def format_field(id, value):
        return f"{id}{len(value):02}{value}"

    chave_field = format_field("01", chave)
    merchant_account = format_field("26", f"0014br.gov.bcb.pix{chave_field}")
    
    payload = "000201" 
    payload += merchant_account
    payload += "52040000" 
    payload += "5303986" 
    payload += format_field("54", f"{valor:.2f}")
    payload += "5802BR" 
    payload += format_field("59", nome[:25]) 
    payload += format_field("60", cidade[:15]) 
    payload += "62070503***" 
    payload += "6304" 

    # Cálculo do CRC16 (Obrigatório para o banco aceitar o código)
    crc = 0xFFFF
    for char in payload:
        crc ^= ord(char) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return payload + f"{crc:04X}"

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def criar_tabelas():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS pecas (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, categoria TEXT, preco REAL, quantidade INTEGER, foto TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS clientes (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, cpf TEXT UNIQUE NOT NULL, telefone TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS notas (id INTEGER PRIMARY KEY AUTOINCREMENT, numero_nota TEXT NOT NULL, data_emissao TEXT NOT NULL, total REAL NOT NULL, itens_json TEXT NOT NULL, cliente_nome TEXT)")

criar_tabelas()

@app.route("/")
def home(): return render_template("home.html")

@app.route("/estoque")
def estoque():
    conn = get_db()
    pecas = conn.execute("SELECT * FROM pecas").fetchall()
    conn.close()
    return render_template("index.html", pecas=pecas)
@app.route("/limpar_carrinho")
def limpar_carrinho():
    session.pop('carrinho', None)
    session.pop('cliente_selecionado', None)
    return redirect(url_for('orcamento'))

@app.route("/cadastrar", methods=["GET", "POST"])
def cadastrar():
    if request.method == "POST":
        f = request.files.get('foto')
        nome_foto = "default.png"
        if f and f.filename != '':
            nome_foto = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{f.filename}")
            f.save(os.path.join(app.config['UPLOAD_FOLDER'], nome_foto))
        
        with get_db() as conn:
            conn.execute("INSERT INTO pecas (nome, categoria, preco, quantidade, foto) VALUES (?, ?, ?, ?, ?)",
                         (request.form["nome"], request.form["categoria"], request.form["preco"], request.form["quantidade"], nome_foto))
        return redirect(url_for("estoque"))
    return render_template("cadastrar.html")

@app.route("/orcamento", methods=["GET", "POST"])
def orcamento():
    conn = get_db()
    pecas = []
    pesquisa = request.form.get('pesquisa', '').strip()
    if pesquisa:
        pecas = conn.execute("SELECT * FROM pecas WHERE nome LIKE ?", ('%' + pesquisa + '%',)).fetchall()
    total = sum(item['subtotal'] for item in session.get('carrinho', []))
    conn.close()
    return render_template("orcamento.html", pecas=pecas, orcamento=session.get('carrinho', []), total=f"{total:.2f}")

@app.route("/adicionar_item", methods=["POST"])
def adicionar_item():
    carrinho = session.get('carrinho', [])
    p, q = float(request.form["preco"]), int(request.form["quantidade"])
    carrinho.append({'nome': request.form["nome"], 'preco': p, 'quantidade': q, 'subtotal': p * q})
    session['carrinho'] = carrinho
    session.modified = True
    return redirect(url_for('orcamento'))

@app.route("/historico", methods=["GET", "POST"])
def historico():
    conn = get_db()
    pesquisa = request.form.get('pesquisa', '').strip()
    query = "SELECT * FROM notas WHERE cliente_nome LIKE ? OR numero_nota LIKE ? ORDER BY id DESC"
    notas = conn.execute(query, ('%' + pesquisa + '%', '%' + pesquisa + '%')).fetchall()
    conn.close()
    return render_template("historico.html", notas=notas)


@app.route('/gerar_nota', methods=['POST'])
def gerar_nota():
    # Recupera itens do carrinho ou orcamento
    itens = session.get('carrinho') or session.get('orcamento', [])
    if not itens: 
        return redirect(url_for('orcamento'))
    
    # Dados da venda
    numero = datetime.now().strftime("%Y%m%d%H%M%S")
    cliente = session.get('cliente_selecionado', 'Consumidor Final')
    total = sum(float(i['subtotal']) for i in itens)
    pagamento = request.form.get('pagamento')
    valor_str = f"{total:.2f}"

    # Salva no Banco de Dados (Padrão SQLite/Flask)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO notas (numero_nota, data_emissao, total, itens_json, cliente_nome) VALUES (?, ?, ?, ?, ?)",
            (numero, datetime.now().strftime("%d/%m/%Y %H:%M"), total, json.dumps(itens), cliente)
        )

    # Fluxo Especial para PIX
    if pagamento == 'Pix':
        # Gera o payload com a sua chave Mercado Pago
        payload_pix = gerar_payload_pix(
            chave="carlinha14.fernandes@gmail.com",
            nome="JM ELETRONICA",
            cidade="RECIFE",
            valor=total
        )
        
        session['pix_data'] = {
            'payload': payload_pix,
            'total': valor_str,
            'numero_nota': numero
        }
        return redirect(url_for('confirmacao_pix'))

    # Fluxo para Dinheiro/Cartão (Gera o PDF direto)
    try:
        from flask_weasyprint import HTML
        logo = os.path.join(app.root_path, 'static', 'img', 'logo.jpg')
        html = render_template('nota_fiscal.html', itens=itens, total=valor_str, 
                               logo=logo, numero_nota=numero, 
                               data=datetime.now().strftime("%d/%m/%Y"), cliente=cliente)
        pdf = HTML(string=html).write_pdf()
        
        session.pop('carrinho', None); session.pop('orcamento', None)
        return send_file(io.BytesIO(pdf), mimetype='application/pdf', 
                         as_attachment=True, download_name=f'nota_{numero}.pdf')
    except Exception as e:
        print(f"Erro PDF: {e}")
        return redirect(url_for('orcamento'))





@app.route('/confirmacao_pix')
def confirmacao_pix():
    data = session.get('pix_data')
    if not data: return redirect(url_for('orcamento'))
    return render_template('confirmacao_pix.html', data=data) 



@app.route("/clientes", methods=["GET", "POST"])
def clientes():
    conn = get_db()
    p = request.form.get('pesquisa', '').strip()
    clis = conn.execute("SELECT * FROM clientes WHERE nome LIKE ? OR cpf LIKE ?", ('%'+p+'%', '%'+p+'%')).fetchall()
    conn.close()
    return render_template("clientes.html", clientes=clis)

@app.route("/selecionar_cliente/<nome>")
def selecionar_cliente(nome):
    session['cliente_selecionado'] = nome
    return redirect(url_for("orcamento"))

@app.route("/cadastrar_cliente", methods=["GET", "POST"])
def cadastrar_cliente():
    if request.method == "POST":
        with get_db() as conn:
            conn.execute("INSERT INTO clientes (nome, cpf, telefone) VALUES (?, ?, ?)", (request.form["nome"], request.form["cpf"], request.form["telefone"]))
        return redirect(url_for("clientes"))
    return render_template("cadastrar_cliente.html")
@app.route('/reimprimir_nota/<int:id>')
def reimprimir_nota(id):
    conn = get_db()
    # Busca os dados da nota salvos no banco pelo ID
    nota = conn.execute("SELECT * FROM notas WHERE id = ?", (id,)).fetchone()
    conn.close()

    if nota:
        # Converte o JSON de volta para lista de dicionários do Python
        itens = json.loads(nota['itens_json'])
        logo = os.path.join(app.root_path, 'static', 'img', 'logo.jpg')
        
        # Renderiza o mesmo HTML usado na geração original
        html = render_template('nota_fiscal.html', 
                               itens=itens, 
                               total=f"{nota['total']:.2f}", 
                               logo=logo, 
                               numero_nota=nota['numero_nota'], 
                               data=nota['data_emissao'].split()[0], # Pega apenas a data
                               cliente=nota['cliente_nome'])
        
        # Gera o PDF usando WeasyPrint
        pdf = HTML(string=html, base_url=os.path.dirname(__file__)).write_pdf()
        
        return send_file(io.BytesIO(pdf), 
                         mimetype='application/pdf', 
                         as_attachment=True, 
                         download_name=f"nota_{nota['numero_nota']}.pdf")
    
    return "Nota não encontrada", 404

@app.route('/loja')
def loja():
    # Esta linha BUSCA os produtos que você cadastrou no banco de dados
    # Se sua classe for 'Peca', use Peca.query.all()
    produtos = Peca.query.all() 
    
    # Busca o carrinho da sessão para não dar erro no resumo lateral
    orcamento = session.get('orcamento', [])
    
    # Calcula o total para exibir no botão de pagamento
    total = sum(float(item.get('subtotal', 0)) for item in orcamento)
    
    # Agora a variável 'produtos' EXISTE e pode ser enviada para o HTML
    return render_template('vitrine.html', 
                           produtos=produtos, 
                           orcamento=orcamento, 
                           total="{:.2f}".format(total))
@app.route('/baixar_pdf/<numero_nota>')
def baixar_pdf(numero_nota):
    with get_db() as conn:
        nota = conn.execute("SELECT * FROM notas WHERE numero_nota = ?", (numero_nota,)).fetchone()
    
    if nota:
        itens = json.loads(nota['itens_json'])
        total_formatado = f"{nota['total']:.2f}"
        logo = os.path.join(app.root_path, 'static', 'img', 'logo.jpg')
        
        from flask_weasyprint import HTML
        import io
        
        html = render_template('nota_fiscal.html', 
                               itens=itens, 
                               total=total_formatado, 
                               logo=logo, 
                               numero_nota=nota['numero_nota'], 
                               data=nota['data_emissao'], 
                               cliente=nota['cliente_nome'])
        
        pdf = HTML(string=html).write_pdf()
        
        # Limpa o carrinho agora que o documento foi gerado
        session.pop('carrinho', None)
        session.pop('orcamento', None)
        
        return send_file(io.BytesIO(pdf), mimetype='application/pdf', as_attachment=True, download_name=f'nota_{numero_nota}.pdf')
    return "Nota não encontrada", 404


if __name__ == "__main__":
    app.run(port=5002, debug=True, use_reloader=False)	
