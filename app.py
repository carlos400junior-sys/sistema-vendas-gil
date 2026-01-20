

from flask import Flask, render_template, request, redirect, url_for, send_file, session
import sqlite3, os, json, io
from datetime import datetime
from weasyprint import HTML
from werkzeug.utils import secure_filename
import json
from flask import request, jsonify
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor


app = Flask(__name__)
from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


app.secret_key = "gil_eletronicos_secret_2025"
DB_NAME = "database.db"
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
def criar_tabelas():
    with get_db() as conn:
        # ... (suas tabelas de peças e notas) ...
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                email TEXT UNIQUE NOT NULL, 
                senha TEXT NOT NULL
            )
        """)


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

# Função para se conectar ao banco PostgreSQL

def get_db():
    # Tenta pegar a URL do Render (Interna - Automática lá no servidor)
    db_url = os.environ.get('DATABASE_URL')
    
    if not db_url:
        # Se estiver no seu PC, ele usa a EXTERNA que você acabou de achar
        db_url = "postgresql://carlos:xXgU9061BpdlJzaOV8jvYJXdNXhsKAnR@dpg-d5ltb0khg0os73c7708g-a.oregon-postgres.render.com/produtos_b64s"
    
    return psycopg2.connect(db_url)




def criar_tabelas():
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Criar Usuários
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                senha TEXT NOT NULL
            );
        """)

        # Criar Peças
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pecas (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                categoria TEXT,
                preco REAL,
                quantidade INTEGER,
                foto TEXT
            );
        """)

        # Criar Clientes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                cpf TEXT UNIQUE NOT NULL,
                telefone TEXT
            );
        """)

        # Criar Notas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notas (
                id SERIAL PRIMARY KEY,
                numero_nota TEXT,
                data_emissao TEXT,
                total REAL,
                itens_json TEXT,
                cliente_nome TEXT
            );
        """)

        conn.commit() # ESSENCIAL para o PostgreSQL
        print("Tabelas verificadas/criadas com sucesso!")
    except Exception as e:
        print(f"Erro ao criar tabelas: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


@app.route("/")
@login_required
def home():
    if "usuario_id" not in session:
        return redirect(url_for("login"))
    return render_template("home.html")
from psycopg2.extras import RealDictCursor # Adicione este import no topo!

@app.route("/estoque")
@login_required
def estoque():
    conn = get_db()
    # Usamos RealDictCursor para poder acessar os dados no HTML como peça['nome']
    cur = conn.cursor(cursor_factory=RealDictCursor) 
    
    cur.execute("SELECT * FROM pecas ORDER BY id DESC")
    pecas = cur.fetchall()
    
    cur.close()
    conn.close()
    return render_template("index.html", pecas=pecas)


@app.route("/cadastrar", methods=["GET", "POST"])
def cadastrar():
    if request.method == "POST":
        f = request.files.get('foto')
        nome_foto = "default.png"
        if f and f.filename != '':
            nome_foto = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{f.filename}")
            f.save(os.path.join(app.config['UPLOAD_FOLDER'], nome_foto))
        
        conn = get_db()
        cur = conn.cursor() # VOCÊ PRECISA DISSO
        try:
            # 1. Use cur.execute (não conn.execute)
            # 2. Use %s (não ?)
            cur.execute("""
                INSERT INTO pecas (nome, categoria, preco, quantidade, foto) 
                VALUES (%s, %s, %s, %s, %s)
            """, (request.form["nome"], request.form["categoria"], 
                  request.form["preco"], request.form["quantidade"], nome_foto))
            
            conn.commit() # OBRIGATÓRIO no Postgres
        except Exception as e:
            conn.rollback()
            print(f"Erro ao salvar peça: {e}")
        finally:
            cur.close()
            conn.close()
            
        return redirect(url_for("estoque"))
    return render_template("cadastrar.html")

from psycopg2.extras import RealDictCursor # Garanta que este import esteja no topo do app.py

@app.route("/orcamento", methods=["GET", "POST"])
def orcamento():
    conn = get_db()
    # Usamos o RealDictCursor para que o pecas.id e pecas.nome funcionem no HTML
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    pecas = []
    pesquisa = request.form.get('pesquisa', '').strip()
    
    if pesquisa:
        # No Postgres usamos %s e o operador ILIKE é melhor (ignora maiúsculas/minúsculas)
        cur.execute("SELECT * FROM pecas WHERE nome ILIKE %s", ('%' + pesquisa + '%',))
        pecas = cur.fetchall()
    
    # Fecha o cursor e a conexão
    cur.close()
    conn.close()
    
    # Cálculo do total do carrinho (mantém a lógica da sessão)
    total = sum(float(item['subtotal']) for item in session.get('carrinho', []))
    
    return render_template("orcamento.html", 
                           pecas=pecas, 
                           orcamento=session.get('carrinho', []), 
                           total=f"{total:.2f}")



@app.route("/historico", methods=["GET", "POST"])
@login_required # Garanta que o decorador esteja aqui se quiser proteção
def historico():
    conn = get_db()
    # Usamos o RealDictCursor para que o HTML consiga ler nota['numero_nota'], etc.
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    pesquisa = request.form.get('pesquisa', '').strip()
    
    # 1. Trocado ? por %s
    # 2. Trocado LIKE por ILIKE (opcional, mas melhor no Postgres para ignorar maiúsculas)
    query = "SELECT * FROM notas WHERE cliente_nome ILIKE %s OR numero_nota ILIKE %s ORDER BY id DESC"
    
    cur.execute(query, ('%' + pesquisa + '%', '%' + pesquisa + '%'))
    notas = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template("historico.html", notas=notas)


@app.route('/gerar_nota', methods=['POST'])
def gerar_nota():
    itens = session.get('carrinho') or session.get('orcamento', [])
    if not itens: 
        return redirect(url_for('orcamento'))
    
    numero = datetime.now().strftime("%Y%m%d%H%M%S")
    cliente = session.get('cliente_selecionado', 'Consumidor Final')
    total = sum(float(i['subtotal']) for i in itens)
    pagamento = request.form.get('pagamento')
    valor_str = f"{total:.2f}"

    # --- CORREÇÃO PARA POSTGRES ---
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO notas (numero_nota, data_emissao, total, itens_json, cliente_nome) VALUES (%s, %s, %s, %s, %s)",
            (numero, datetime.now().strftime("%d/%m/%Y %H:%M"), total, json.dumps(itens), cliente)
        )
        conn.commit() # Salva no banco
    except Exception as e:
        conn.rollback()
        print(f"Erro ao salvar nota: {e}")
        return "Erro ao processar venda", 500
    finally:
        cur.close()
        conn.close()
    # ------------------------------

    if pagamento == 'Pix':
        payload_pix = gerar_payload_pix(
            chave="carlinha14.fernandes@gmail.com",
            nome="JM ELETRONICA",
            cidade="RECIFE",
            valor=total
        )
        session['pix_data'] = {'payload': payload_pix, 'total': valor_str, 'numero_nota': numero}
        return redirect(url_for('confirmacao_pix'))

    # Para outros pagamentos, gera o PDF
    try:
        logo = os.path.join(app.root_path, 'static', 'img', 'logo.jpg')
        html = render_template('nota_fiscal.html', itens=itens, total=valor_str, 
                               logo=logo, numero_nota=numero, 
                               data=datetime.now().strftime("%d/%m/%Y"), cliente=cliente)
        pdf = HTML(string=html).write_pdf()
        
        session.pop('carrinho', None)
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
    # Usamos RealDictCursor para o HTML ler cliente.nome e cliente.cpf corretamente
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    p = request.form.get('pesquisa', '').strip()
    
    # No Postgres usamos %s e ILIKE para busca flexível
    query = "SELECT * FROM clientes WHERE nome ILIKE %s OR cpf ILIKE %s ORDER BY nome"
    cur.execute(query, ('%'+p+'%', '%'+p+'%'))
    clis = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template("clientes.html", clientes=clis)


@app.route("/selecionar_cliente/<nome>")
def selecionar_cliente(nome):
    session['cliente_selecionado'] = nome
    return redirect(url_for("orcamento"))

@app.route("/cadastrar_cliente", methods=["GET", "POST"])
@login_required
def cadastrar_cliente():
    if request.method == "POST":
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO clientes (nome, cpf, telefone) VALUES (%s, %s, %s)",
                (request.form["nome"], request.form["cpf"], request.form["telefone"])
            )
            conn.commit() # SALVA NO POSTGRES
        except Exception as e:
            conn.rollback()
            print(f"Erro ao cadastrar cliente: {e}")
        finally:
            cur.close()
            conn.close()
        return redirect(url_for("clientes"))
    return render_template("cadastrar_cliente.html")
@app.route('/adicionar_item', methods=['POST'])
def adicionar_item():
    # Pega o que está na sessão. Se não existir, garante uma lista REALMENTE nova []
    # Usar list() garante que não estamos pegando uma referência fantasma
    itens_atuais = list(session.get('orcamento', []))

    nome = request.form.get('nome')
    preco = float(request.form.get('preco'))
    quantidade = int(request.form.get('quantidade', 1))

    item_encontrado = False
    for item in itens_atuais:
        if item['nome'] == nome:
            item['quantidade'] += quantidade
            item['subtotal'] = item['quantidade'] * item['preco']
            item_encontrado = True
            break
    
    if not item_encontrado:
        itens_atuais.append({
            'nome': nome,
            'preco': preco,
            'quantidade': quantidade,
            'subtotal': preco * quantidade
        })

    # Sobrescreve a sessão com a lista nova
    session['orcamento'] = itens_atuais
    session.modified = True

    total_geral = sum(item['subtotal'] for item in itens_atuais)

    return jsonify({
        'carrinho': itens_atuais,
        'total': f"{total_geral:.2f}"
    })





@app.route("/adicionar_itens_cliente", methods=["POST"])
def adicionar_itens_cliente():
    # Pega o orçamento atual da sessão (usando o nome que a rota /loja usa)
    orcamento = session.get('orcamento', [])
    
    # Pega os dados enviados pelo formulário
    nome = request.form.get("nome")
    preco = request.form.get("preco", 0)
    quantidade = request.form.get("quantidade", 1)
    # Pega a foto para exibir no carrinho lateral
    foto = request.form.get("imagem") 
    
    try:
        preco = float(preco)
        quantidade = int(quantidade)
        if preco <= 0 or quantidade <= 0:
            raise ValueError("Preço e quantidade devem ser maiores que zero.")
    except ValueError:
        flash("Dados inválidos. Por favor, verifique o preço e a quantidade.", "error")
        return redirect(url_for('loja') + '#carrinho')
    
    # Verifica se o item já existe no orçamento
    item_existente = next((item for item in orcamento if item['nome'] == nome), None)
    
    if item_existente:
        # Se já existir, atualiza a quantidade
        item_existente['quantidade'] += quantidade
    else:
        # Caso contrário, adiciona o novo item com a chave 'imagem' para o HTML renderizar
        orcamento.append({
            'nome': nome,
            'preco': preco,
            'quantidade': quantidade,
            'imagem': foto
        })
    
    # Salva de volta na sessão como 'orcamento'
    session['orcamento'] = orcamento
    session.modified = True
    
    # Redireciona para a vitrine (rota loja) focando na div #carrinho
    return redirect(url_for('loja') + '#resumo-venda')



@app.route("/limpar_carrinho")
def limpar_carrinho():
    # Limpa as chaves e força a limpeza física da sessão
    session.pop('orcamento', None)
    session.pop('carrinho', None)
    session.pop('pix_data', None)
    
    # Isso limpa qualquer resquício de dicionário da sessão
    session.modified = True 
    
    # Opcional: limpa TUDO da sessão se nada mais for importante
    # session.clear() 

    return redirect(url_for('orcamento'))





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
    try:
        conn = get_db()
        # O DictCursor faz o psycopg2 retornar os dados como dicionário
        from psycopg2.extras import RealDictCursor
        cursor = conn.cursor(cursor_factory=RealDictCursor) 

        cursor.execute("SELECT * FROM pecas")
        produtos_db = cursor.fetchall() 

        cursor.close()
        conn.close()

        orcamento = session.get('orcamento', [])
        # Certifique-se de que o subtotal é calculado corretamente
        total = sum(float(item.get('preco', 0)) * int(item.get('quantidade', 1)) for item in orcamento)

        return render_template('vitrine.html', 
                               produtos=produtos_db, 
                               orcamento=orcamento, 
                               total="{:.2f}".format(total))

    except Exception as e:
        print(f"Erro detalhado na rota '/loja': {e}")
        return f"Erro interno: {e}", 500





@app.route('/baixar_pdf/<numero_nota>')
def baixar_pdf(numero_nota):
    conn = get_db()
    # Usamos RealDictCursor para que o 'nota' se comporte como um dicionário
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. Trocado para cur.execute
        # 2. Trocado ? por %s
        cur.execute("SELECT * FROM notas WHERE numero_nota = %s", (numero_nota,))
        nota = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    
    if nota:
        itens = json.loads(nota['itens_json'])
        total_formatado = f"{nota['total']:.2f}"
        logo = os.path.join(app.root_path, 'static', 'img', 'logo.jpg')
        
        # Renderiza o template da nota
        html = render_template('nota_fiscal.html', 
                               itens=itens, 
                               total=total_formatado, 
                               logo=logo, 
                               numero_nota=nota['numero_nota'], 
                               data=nota['data_emissao'], 
                               cliente=nota['cliente_nome'])
        
        # Gera o PDF usando o WeasyPrint importado no topo
        pdf_gerado = HTML(string=html).write_pdf()
        
        # Limpa as sessões de venda após o download
        session.pop('carrinho', None)
        session.pop('orcamento', None)
        
        return send_file(
            io.BytesIO(pdf_gerado), 
            mimetype='application/pdf', 
            as_attachment=True, 
            download_name=f'nota_{numero_nota}.pdf'
        )
    
    return "Nota não encontrada", 404

#ROTA PARA GERAR LOGIN 

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        senha = request.form.get("senha")
        
        conn = get_db()
        cur = conn.cursor() # Você PRECISA criar um cursor no PostgreSQL
        
        cur.execute("SELECT * FROM usuarios WHERE email = %s AND senha = %s", (email, senha))
        user = cur.fetchone()
        
        cur.close()
        conn.close()
        
        if user:
            session["usuario_id"] = user[0] # No Postgres/psycopg2, o acesso costuma ser por índice
            return redirect(url_for("home"))
        
        return "Login inválido!"
    return render_template("login.html")

#ROTA PARA CADASTRAR USUARIO
@app.route("/registrar", methods=["GET", "POST"])
def registrar():
    if request.method == "POST":
        email = request.form.get("email")
        senha = request.form.get("senha")
        
        conn = get_db()
        cur = conn.cursor()
        try:
            # Use %s para o Postgres, não ?
            cur.execute("INSERT INTO usuarios (email, senha) VALUES (%s, %s)", (email, senha))
            conn.commit() # Sem isso, o usuário não é salvo!
            return redirect(url_for('login'))
        except Exception as e:
            conn.rollback()
            print(f"ERRO NO REGISTRO: {e}") # Olhe o terminal aqui!
            return "Erro: E-mail já existe ou falha na conexão."
        finally:
            cur.close()
            conn.close()
    return render_template("registrar.html")

@app.route('/logout')
def logout():
    session.clear() # Limpa o usuario_id e o carrinho
    return redirect(url_for('login'))

@app.route('/remover_item/<int:indice>')
def remover_item(indice):
    # Recupera o carrinho da sessão
    orcamento = session.get('orcamento', [])
    
    # Verifica se o índice é válido e remove
    if 0 <= indice < len(orcamento):
        orcamento.pop(indice)
        session['orcamento'] = orcamento
        session.modified = True # Avisa ao Flask que a sessão mudou
        
    # Redireciona de volta para a loja (ajuste 'loja' se o nome da sua rota for outro)
    return redirect(url_for('loja') + '#carrinho')

@app.route('/excluir_peca/<int:id>', methods=['POST'])
@login_required
def excluir_peca(id):
    conn = get_db()
    cur = conn.cursor()
    try:
        # 1. Busca a foto para deletar do servidor também
        cur.execute("SELECT foto FROM pecas WHERE id = %s", (id,))
        resultado = cur.fetchone()
        
        # 2. Deleta do banco de dados
        cur.execute("DELETE FROM pecas WHERE id = %s", (id,))
        conn.commit()

        # 3. Se tinha foto, apaga o arquivo físico
        if resultado and resultado[0]:
            caminho_foto = os.path.join(app.root_path, 'static/uploads', resultado[0])
            if os.path.exists(caminho_foto):
                os.remove(caminho_foto)

    except Exception as e:
        conn.rollback()
        print(f"Erro ao excluir: {e}")
    finally:
        cur.close()
        conn.close()
    
    # 🎯 Aqui está o segredo: redireciona para a FUNÇÃO estoque, não para o arquivo html
    return redirect(url_for('estoque'))




if __name__ == "__main__":
    app.run(port=5002, debug=True, use_reloader=False)	
