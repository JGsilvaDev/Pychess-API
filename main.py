from fastapi import FastAPI, Depends, HTTPException, Security, Header, BackgroundTasks, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.openapi.models import APIKey
from fastapi.openapi.utils import get_openapi
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import desc
from sqlalchemy.orm import Session
from database.database import SessionLocal, engine
from stockfish import Stockfish
from passlib.hash import bcrypt
from database.database import get_db 
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from fastapi.responses import JSONResponse
from jwt import ExpiredSignatureError, DecodeError
from uuid import uuid4
from starlette.status import HTTP_400_BAD_REQUEST
from chess import Board

from Model.users import User
from Model.games import Game
from Model.moves import Move
from Model.evaluation import Evaluation
from Model.robotToken import RobotToken

import jwt
import math
import time
import math
import os
import smtplib
import chess
import chess.engine
import socketio
import asyncio
from typing import Dict

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

app = FastAPI(
    title="Pychess",
    description="API com autenticação JWT",
    version="1.0",
    openapi_tags=[{"name": "DB", "description": "Rotas que acessam o banco de dados"}],
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configurar os domínios permitidos (origens permitidas)
origins = [
    "http://localhost:3000",  # Frontend Next.js em desenvolvimento
    "http://127.0.0.1:3000",  # Outra variação do localhost
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # ou ["*"] se for só pra testes
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],  # <- isso é o importante pro Authorization!
)

app_socket = socketio.ASGIApp(sio, other_asgi_app=app)

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="Pychess",
        version="1.0",
        description="API com autenticação JWT",
        routes=app.routes,
    )
    openapi_schema["components"] = {
        "securitySchemes": {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT"
            }
        }
    }
    openapi_schema["security"] = [{"BearerAuth": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

load_dotenv(override=True)

STOCKFISH_PATH = r"C:\Users\joao.silva\OneDrive - Allparts Componentes Ltda\Documentos\GitHub\Pychess-API\stockfish\stockfish-windows-x86-64-avx2.exe"
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")

security = HTTPBearer()

# Inicializa o motor Stockfish
stockfish = Stockfish(STOCKFISH_PATH)
stockfish.set_skill_level(10)  # Ajuste o nível de habilidade (0-20)
stockfish.set_depth(15)  # Profundidade de busca

# Variável para armazenar o histórico do jogo
board = chess.Board()

modo_robo_ativo = False

def create_reset_token(email: str):
    """ Gera um token JWT para redefinição de senha """
    expire = datetime.utcnow() + timedelta(minutes=30)
    data = {"sub": email, "exp": expire}
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

def send_reset_email(email: str, token: str):
    """ Envia o e-mail com o link para redefinir senha """
    reset_link = f"http://localhost:8000/reset-password?token={token}"
    
    msg = MIMEMultipart()
    msg["From"] = os.getenv("SMTP_EMAIL")
    msg["To"] = email
    msg["Subject"] = "Redefinição de Senha"
    
    body = f"""
    <p>Olá,</p>
    <p>Você solicitou a redefinição de senha. Clique no link abaixo para redefinir sua senha:</p>
    <p><a href="{reset_link}">Redefinir Senha</a></p>
    <p>Este link expira em {30} minutos.</p>
    """
    
    msg.attach(MIMEText(body, "html"))

    try:
        server = smtplib.SMTP(os.getenv("SMTP_SERVER"), os.getenv("SMTP_PORT"))
        server.starttls()
        server.login(os.getenv("SMTP_EMAIL"), os.getenv("SMTP_PASSWORD"))
        server.sendmail(os.getenv("SMTP_EMAIL"), email, msg.as_string())
        server.quit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao enviar e-mail: {str(e)}")

# Lista global para armazenar até 3 últimas partidas
game_history = []

def fen_to_matrix(fen):
    """Converte um FEN em uma matriz 8x8 representando o tabuleiro."""
    rows = fen.split(" ")[0].split("/")  # Pegamos apenas a parte do tabuleiro no FEN
    board_matrix = []

    for row in rows:
        board_row = []
        for char in row:
            if char.isdigit():
                board_row.extend(["."] * int(char))  # Espaços vazios
            else:
                board_row.append(char)  # Peça
        board_matrix.append(board_row)

    return board_matrix

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(get_db)):
    token = credentials.credentials

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        user_id = payload.get("id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

@app.post("/set_difficulty/",tags=['GAME'])
def set_difficulty(level: str):
    """Define o nível de dificuldade do Stockfish"""
    
    difficulty_settings = {
        "muito_baixa": {"skill": 1, "depth": 2, "rating": 150},
        "baixa": {"skill": 2, "depth": 4, "rating": 300},
        "media": {"skill": 5, "depth": 8, "rating": 600},
        "dificil": {"skill": 10, "depth": 14, "rating": 1200},
        "extremo": {"skill": 20, "depth": 22, "rating": "MAX"}
    }

    level = level.lower()
    
    if level not in difficulty_settings:
        raise HTTPException(status_code=400, detail="Nível inválido! Escolha entre: muito_baixa, baixa, media, dificil, extremo.")

    settings = difficulty_settings[level]

    stockfish.set_skill_level(settings["skill"])
    stockfish.set_depth(settings["depth"])

    return {
        "message": f"Dificuldade ajustada para '{level}'",
        "skill_level": settings["skill"],
        "depth": settings["depth"],
        "rating": settings["rating"]
    }

@app.post("/start_game/", tags=['GAME'])
def start_game(user_id: int,  background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """ Inicia um novo jogo de xadrez e registra a posição inicial. """
    
    # Verifica se há algum jogo em andamento
    existing_game = db.query(Game).filter(Game.player_win == 0).first()
    if existing_game:
        raise HTTPException(status_code=400, detail="Já existe um jogo em andamento!")

    # Criar um novo jogo
    new_game = Game(user_id=user_id)
    db.add(new_game)
    db.commit()
    db.refresh(new_game)

    # Iniciar posição no Stockfish
    stockfish.set_position([])  # posição inicial padrão

    # Criar jogada inicial na tabela moves
    initial_move = Move(
        is_player=None,  # Nenhuma jogada ainda
        move="",  # Movimento vazio (início do jogo)
        board_string=stockfish.get_fen_position(),  # FEN da posição inicial
        mv_quality=None,  # Não se aplica ainda
        game_id=new_game.id
    )
    db.add(initial_move)
    db.commit()

    new_eval = Evaluation(
            game_id=new_game.id,
            evaluation=0,
            depth=0,
            win_probability_white=50,
            win_probability_black=50,
        )
    db.add(new_eval)
    db.commit()

    return {
        "message": "Jogo iniciado!",
        "game_id": new_game.id,
        "board": stockfish.get_board_visual()
    }

@app.post("/load_game/", tags=['GAME'])
def load_game(game_id: int, db: Session = Depends(get_db)):
    """ Carrega um jogo salvo do banco de dados e atualiza o tabuleiro. """
    
    # Busca o jogo pelo ID
    game = db.query(Game).filter(Game.id == game_id).first()
    
    if not game:
        raise HTTPException(status_code=404, detail="Jogo não encontrado!")

    # Obtém os movimentos associados ao jogo, ordenados pela sequência correta
    moves = db.query(Move.move).filter(Move.game_id == game.id).order_by(Move.id).all()
    moves = [m.move for m in moves]  # Converte para uma lista de strings

    # Configura o Stockfish com os movimentos do jogo carregado
    stockfish.set_position(moves)

    return {
        "message": f"Jogo {game_id} carregado!",
        "board": stockfish.get_board_visual().split("\n")  # Divide em linhas para exibição
    }

@app.get("/game_state_per_moviment/", tags=['GAME'])
def get_game_state_per_moviment(game_id: int, move_number: int, db: Session = Depends(get_db)):
    """ Retorna o estado do tabuleiro após um número específico de jogadas. """
    
    # Busca o jogo pelo ID
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Jogo não encontrado!")

    # Obtém os movimentos até o número especificado
    moves = (
        db.query(Move.move)
        .filter(Move.game_id == game.id)
        .order_by(Move.id)
        .limit(move_number)
        .all()
    )
    moves = [m.move for m in moves]  # Converte para lista de strings

    # Se não houver jogadas, retorna o tabuleiro inicial
    if not moves:
        stockfish.set_position([])  # Reseta o tabuleiro
    else:
        stockfish.set_position(moves)

    return {
        "message": f"Jogo {game_id} após {move_number} jogadas.",
        "board": stockfish.get_board_visual().split("\n")  # Divide para exibição
    }

@app.get("/game_moves/{game_id}", tags=["GAME"])
def get_game_moves_by_id(game_id: int, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Jogo não encontrado.")

    moves = db.query(Move).filter(Move.game_id == game.id).order_by(Move.id).all()
    move_list = [m.move for m in moves]

    return {"moves": move_list}

@app.get("/game_board/", tags=['GAME'])
def get_game_board(db: Session = Depends(get_db)):
    """ Retorna a visualização do tabuleiro baseado no último estado salvo no banco. """

    # Obtém o último jogo ativo e seu último movimento em uma única consulta
    last_game = (
        db.query(Game.id, Move.board_string)
        .join(Move, Move.game_id == Game.id)
        .filter(Game.player_win == 0)
        .order_by(Game.id.desc(), Move.id.desc())
        .first()
    )

    if not last_game:
        raise HTTPException(status_code=404, detail="Nenhum jogo ativo ou jogada encontrada.")

    game_id, fen_string = last_game

    # Validação do FEN antes de enviar para o Stockfish
    if not fen_string or len(fen_string.split()) != 6:
        raise HTTPException(status_code=400, detail="FEN inválido no banco de dados.")

    # Define a posição no Stockfish
    try:
        stockfish.set_fen_position(fen_string)
        board_visual = stockfish.get_board_visual()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao processar tabuleiro: {str(e)}")

    if not board_visual:
        raise HTTPException(status_code=500, detail="Falha ao gerar visualização do tabuleiro.")

    return {
        "board": board_visual.split("\n"),
        "fen": fen_string
    }

@app.post("/play_game/", tags=['GAME'])
async def play_game(move: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """ O usuário joga, e o Stockfish responde com a melhor jogada, verificando capturas. """

    # Verifica se há um jogo ativo
    game = db.query(Game).filter(Game.player_win == 0).first()
    if not game:
        raise HTTPException(status_code=400, detail="Nenhum jogo ativo encontrado!")

    # Obtém o último estado salvo do tabuleiro
    last_move = db.query(Move).filter(Move.game_id == game.id).order_by(Move.id.desc()).first()

    # Se houver um estado salvo, carregamos ele; caso contrário, criamos um novo tabuleiro
    board = chess.Board(last_move.board_string) if last_move and last_move.board_string else chess.Board()

    # Verifica se a jogada do jogador é válida
    if move not in [m.uci() for m in board.legal_moves]:
        raise HTTPException(status_code=400, detail="Movimento do jogador inválido!")

    # Aplica o movimento do jogador no tabuleiro
    board.push(chess.Move.from_uci(move))

    # Atualiza o Stockfish com o novo estado do jogo
    stockfish.set_fen_position(board.fen())

    # Análise da jogada
    analysis = analyze_move(move, db)
    classification = analysis["classification"]

    # Salva o movimento do jogador no banco
    new_move = Move(
        is_player=True,
        move=move,
        board_string=board.fen(),
        mv_quality=classification,
        game_id=game.id,
        created_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )
    db.add(new_move)
    db.commit()

    # Verifica xeque-mate após o movimento do jogador
    if board.is_checkmate():
        game.player_win = 1  # Brancas venceram
        db.commit()
        rating(game.user_id)

        return {
            "message": "Xeque-mate! Brancas venceram!",
            "board_fen": board.fen(),
            "player_move": move,
            "stockfish_move": None
        }

    # Stockfish responde com o melhor movimento
    best_move = stockfish.get_best_move()
    if best_move:
        stockfish_move = chess.Move.from_uci(best_move)

        # Se for válido, aplicamos no tabuleiro
        if stockfish_move in board.legal_moves:
            board.push(stockfish_move)
            stockfish.set_fen_position(board.fen())

            # Salva a jogada do Stockfish
            sf_move = Move(
                is_player=False,
                move=best_move,
                board_string=board.fen(),
                game_id=game.id,
                mv_quality=None,
                created_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
            db.add(sf_move)
            db.commit()

            # Verifica xeque-mate após a jogada do Stockfish
            if board.is_checkmate():
                game.player_win = 2  # Pretas venceram
                db.commit()
                rating(game.user_id)

                return {
                    "message": "Xeque-mate! Pretas venceram!",
                    "board_fen": board.fen(),
                    "player_move": move,
                    "stockfish_move": best_move
                }
        else:
            return {
                "message": "Movimento do Stockfish inválido. Tentando novamente...",
                "board_fen": board.fen(),
                "player_move": move,
                "stockfish_move": None
            }
        
    background_tasks.add_task(calculate_and_save_evaluation, game.id, db)

    await sio.emit("board_updated")

    return {
        "message": "Movimentos realizados!",
        "board_fen": board.fen(),
        "player_move": move,
        "stockfish_move": best_move
    }

def calculate_and_save_evaluation(game_id: int, db: Session):
    moves = db.query(Move.move).filter(Move.game_id == game_id).order_by(Move.id).all()
    move_list = [m.move for m in moves]
    stockfish.set_position(move_list)

    best_eval = None
    best_depth = 0

    for depth in range(8, 13):
        stockfish.set_depth(depth)
        evaluation = stockfish.get_evaluation()

        if best_eval is None or abs(evaluation["value"]) > abs(best_eval["value"]):
            best_eval = evaluation
            best_depth = depth

    if best_eval["type"] == "mate":
        if best_eval["value"] > 0:
            win_white = 100
        else:
            win_white = 0
    else:
        cp = best_eval["value"]
        win_white = round((1 / (1 + math.exp(-0.004 * cp))) * 100, 2)

    win_black = round(100 - win_white, 2)

    existing = db.query(Evaluation).filter(Evaluation.game_id == game_id).first()
    if existing:
        existing.evaluation = best_eval["value"]
        existing.depth = best_depth
        existing.win_probability_white = win_white
        existing.win_probability_black = win_black
        existing.last_updated = datetime.utcnow()
    else:
        new_eval = Evaluation(
            game_id=game_id,
            evaluation=best_eval["value"],
            depth=best_depth,
            win_probability_white=win_white,
            win_probability_black=win_black,
        )
        db.add(new_eval)

    db.commit()

@app.get("/evaluate_position/", tags=['GAME'])
def evaluate_position(db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.player_win == 0).first()
    if not game:
        raise HTTPException(status_code=404, detail="Nenhum jogo ativo encontrado.")

    evaluation = db.query(Evaluation).filter(Evaluation.game_id == game.id).first()
    if not evaluation:
        raise HTTPException(status_code=404, detail="Nenhuma avaliação disponível ainda.")

    return {
        "evaluation": evaluation.evaluation,
        "best_depth": evaluation.depth,
        "win_probability_white": evaluation.win_probability_white,
        "win_probability_black": evaluation.win_probability_black,
        "last_updated": evaluation.last_updated,
    }

@app.get("/game_moves/", tags=["GAME"])
def get_game_moves(db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.player_win == 0).first()
    if not game:
        raise HTTPException(status_code=404, detail="Nenhum jogo ativo encontrado.")
    
    moves = db.query(Move).filter(Move.game_id == game.id).order_by(Move.id).all()
    move_list = [m.move for m in moves]
    
    return {"moves": move_list}

@app.post("/rating/", tags=['GAME'])
def rating(user_id: int, db: Session = Depends(get_db)):
    """Avalia o jogo completo armazenado em game_moves e atualiza o rating do jogador no banco de dados."""

    global stockfish

    game = db.query(Game).filter(Game.player_win == 0).first()

    if not game:
        raise HTTPException(status_code=400, detail="Nenhum jogo ativo encontrado!")

    # Obtém os movimentos já registrados no banco para este jogo
    game_moves = db.query(Move.move).filter(Move.game_id == game.id).all()
    game_moves = [m.move for m in game_moves]  # Transformando em lista de strings

    # Verifica se há jogadas para avaliar
    if not game_moves:
        raise HTTPException(status_code=400, detail="Nenhuma jogada registrada para avaliação.")

    # Busca o usuário e seu rating atual
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado!")

    base_rating = user.rating  # Rating atual do jogador
    rating = base_rating  # Inicializa o rating com o valor do banco

    stockfish.set_position([])  # Reseta o Stockfish para o início da partida

    for i, move in enumerate(game_moves):
        if not stockfish.is_move_correct(move):
            raise HTTPException(status_code=400, detail=f"Movimento inválido detectado: {move}")

        stockfish.set_position(game_moves[:i + 1])  # Atualiza posição até a jogada atual

        best_move = stockfish.get_best_move()  # Melhor jogada segundo Stockfish
        evaluation_before = stockfish.get_evaluation()  # Avaliação antes do movimento
        stockfish.make_moves_from_current_position([move])  # Aplica o movimento no Stockfish
        evaluation_after = stockfish.get_evaluation()  # Avaliação depois do movimento
        
        eval_diff = evaluation_before["value"] - evaluation_after["value"]

        if best_move == move:
            rating += 50  # Jogada perfeita
        elif eval_diff > 200:
            rating -= 50  # Erro grave (Blunder)
        elif eval_diff > 100:
            rating -= 20  # Jogada imprecisa
        elif eval_diff > 30:
            rating -= 5   # Pequeno erro
        else:
            rating += 5   # Jogada sólida

    # Garante que o rating final não fique negativo
    final_rating = max(0, rating)

    # Calcula a diferença entre o rating final e o atual do jogador
    rating_diff = final_rating - base_rating

    # Atualiza o rating no banco de dados conforme a diferença
    if rating_diff >= 200:
        user.rating += 100
    elif rating_diff >= 100:
        user.rating += 70
    elif rating_diff >= 20:
        user.rating += 50
    elif rating_diff > 0:
        user.rating += 20

    db.commit()

    return {
        "message": "Avaliação concluída!",
        "final_rating": final_rating,
        "rating_updated": user.rating, 
        "moves_analyzed": len(game_moves)
    }

@app.post("/analyze_move/",tags=['GAME'])
def analyze_move(move: str,  db: Session = Depends(get_db)):
    """ Analisa a jogada, comparando com a melhor possível. """

     # Verifica se existe um jogo ativo
    game = db.query(Game).filter(Game.player_win == 0).first()

    if not game:
        raise HTTPException(status_code=400, detail="Nenhum jogo ativo encontrado!")

    # Obtém os movimentos já registrados no banco para este jogo
    game_moves = db.query(Move.move).filter(Move.game_id == game.id).all()
    game_moves = [m.move for m in game_moves]  # Transformando em lista de strings

    stockfish.set_position(game_moves)

    # Obtém a melhor jogada recomendada pelo Stockfish
    best_move = stockfish.get_best_move()

    if not stockfish.is_move_correct(move):
        raise HTTPException(status_code=400, detail="Movimento inválido!")

    # Avaliação antes da jogada
    eval_before = stockfish.get_evaluation()
    eval_before_score = eval_before["value"] if eval_before["type"] == "cp" else 0

    # Aplica o movimento do usuário
    game_moves.append(move)
    stockfish.set_position(game_moves)

    # Avaliação após a jogada
    eval_after = stockfish.get_evaluation()
    eval_after_score = eval_after["value"] if eval_after["type"] == "cp" else 0

    # Desfaz o movimento do usuário e testa a melhor jogada do Stockfish
    game_moves.pop()
    stockfish.set_position(game_moves)
    game_moves.append(best_move)
    stockfish.set_position(game_moves)

    # Avaliação após a melhor jogada do Stockfish
    eval_best = stockfish.get_evaluation()
    eval_best_score = eval_best["value"] if eval_best["type"] == "cp" else 0

    # Calcula a diferença entre as avaliações
    diff_user = eval_after_score - eval_before_score  # O quanto a jogada do usuário melhorou ou piorou a posição
    diff_best = eval_best_score - eval_before_score  # O quanto a melhor jogada melhoraria a posição
    diff_to_best = diff_user - diff_best  # Diferença entre a jogada do usuário e a melhor jogada

    # Classificação da jogada
    if diff_to_best == 0:
        classification = "Brilhante 💎"
    elif -30 <= diff_to_best < 0:
        classification = "Boa ✅"
    elif -100 <= diff_to_best < -30:
        classification = "Ok 🤷"
    else:
        classification = "Gafe ❌"

    return {
        "move": move,
        "best_move": best_move,
        "evaluation_before": eval_before_score,
        "evaluation_after": eval_after_score,
        "evaluation_best_move": eval_best_score,
        "classification": classification,
        "board": stockfish.get_board_visual()
    }

@app.get("/game_history/",tags=['GAME'])
def game_history(db: Session = Depends(get_db)):
    """ Retorna o histórico de jogadas do jogo atual. """
    game = db.query(Game).filter(Game.player_win == 0).first()

    if not game:
        raise HTTPException(status_code=400, detail="Nenhum jogo ativo encontrado!")

    # Obtém os movimentos já registrados no banco para este jogo
    game_moves = db.query(Move.move).filter(Move.game_id == game.id).all()
    game_moves = [m.move for m in game_moves]  # Transformando em lista de strings

    return {"moves": game_moves}

@app.get("/last_game/", tags=["GAME"])
def get_last_game(db: Session = Depends(get_db)):
    """
    Retorna a última partida concluída.
    """
    # Busca a última partida onde já houve vencedor (player_win diferente de 0)
    last_game = (
        db.query(Game)
        # .filter(Game.player_win != 0)
        .order_by(Game.id.desc())
        .first()
    )

    if not last_game:
        return JSONResponse(content={"detail": "Nenhuma partida encontrada."}, status_code=404)

    user = db.query(User).filter(User.id == last_game.user_id).first()
    username = user.username if user else "Desconhecido"

    # Determina o resultado
    result = "Derrota" if last_game.player_win == 2 else "Vitória"

    # Calcular duração da partida
    first_move = (
        db.query(Move)
        .filter(Move.game_id == last_game.id)
        .order_by(Move.created_at.asc())
        .first()
    )
    last_move = (
        db.query(Move)
        .filter(Move.game_id == last_game.id)
        .order_by(Move.created_at.desc())
        .first()
    )

    if first_move and last_move:
        fmt = '%Y-%m-%d %H:%M:%S'
        first_dt = datetime.strptime(first_move.created_at, fmt)
        last_dt = datetime.strptime(last_move.created_at, fmt)
        duration = last_dt - first_dt
        total_seconds = int(duration.total_seconds())

        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        duration_str = "00:00:00"

    return {
        "username": username,
        "result": result,
        "duration": duration_str,
        "id": last_game.id
    }

@app.post("/evaluate_progress/", tags=['GAME'])
def evaluate_progress(db: Session = Depends(get_db)):
    """Compara as três últimas partidas e verifica a evolução do jogador."""
    global game_history

    game = db.query(Game).filter(Game.player_win == 0).first()

    if not game:
        raise HTTPException(status_code=400, detail="Nenhum jogo ativo encontrado!")

    # Obtém os movimentos já registrados no banco para este jogo
    game_moves = db.query(Move.move).filter(Move.game_id == game.id).all()
    game_moves = [m.move for m in game_moves]  # Transformando em lista de strings

    if not game_moves:
        raise HTTPException(status_code=400, detail="Nenhuma partida registrada para avaliação.")

    # Adiciona a última partida ao histórico
    if len(game_history) >= 3:
        game_history.pop(0)  # Remove a mais antiga para manter apenas 3 partidas

    game_history.append(list(game_moves))  # Salva o jogo atual

    if len(game_history) < 3:
        return {"message": "Ainda não há partidas suficientes para análise. Jogue pelo menos 3 partidas!"}

    def analyze_game(moves):
        """Analisa uma partida e retorna estatísticas de qualidade."""
        stockfish.set_position([])
        good_moves, blunders, total_moves = 0, 0, len(moves)

        for i, move in enumerate(moves):
            stockfish.set_position(moves[:i + 1])

            best_move = stockfish.get_best_move()
            evaluation_before = stockfish.get_evaluation()
            stockfish.make_moves_from_current_position([move])
            evaluation_after = stockfish.get_evaluation()

            eval_diff = evaluation_before["value"] - evaluation_after["value"]

            if best_move == move:
                good_moves += 1  # Jogada perfeita
            elif eval_diff > 200:
                blunders += 1  # Erro grave
            elif eval_diff > 100:
                blunders += 0.5  # Pequeno erro

        return {
            "good_moves": good_moves,
            "blunders": blunders,
            "total_moves": total_moves
        }

    # Analisa as três últimas partidas
    analysis = [analyze_game(game) for game in game_history]

    def calc_percentage_change(old, new):
        """Calcula a porcentagem de mudança entre duas partidas."""
        if old == 0:
            return 100 if new > 0 else 0  # Se não houver referência anterior
        return round(((new - old) / old) * 100, 2)

    # Compara a última partida com as duas anteriores
    progress = {
        "improvement_from_last": {
            "good_moves": calc_percentage_change(analysis[1]["good_moves"], analysis[2]["good_moves"]),
            "blunders": calc_percentage_change(analysis[1]["blunders"], analysis[2]["blunders"]),
            "total_moves": calc_percentage_change(analysis[1]["total_moves"], analysis[2]["total_moves"])
        },
        "improvement_from_two_games_ago": {
            "good_moves": calc_percentage_change(analysis[0]["good_moves"], analysis[2]["good_moves"]),
            "blunders": calc_percentage_change(analysis[0]["blunders"], analysis[2]["blunders"]),
            "total_moves": calc_percentage_change(analysis[0]["total_moves"], analysis[2]["total_moves"])
        }
    }

    return {
        "message": "Comparação realizada!",
        "progress": progress
    }

class MoveRequest(BaseModel):
    move: str

games = {}
active_games: Dict[str, chess.Board] = {}

@app.post("/play_autonomous_game/", tags=['GAME'])
async def play_autonomous_game(move_req: MoveRequest, game_id: str = Query(...)):
    move = move_req.move

    global active_games

    if game_id not in active_games:
        active_games[game_id] = chess.Board()

    board = active_games[game_id]

    if board.is_game_over():
        board.reset()

    if move not in [m.uci() for m in board.legal_moves]:
        return {
            "fen": board.fen(),
            "status": "invalid",
            "error": "Movimento do jogador inválido!",
            "player_move": move,
            "stockfish_move": None
        }

    board.push(chess.Move.from_uci(move))

    if board.is_checkmate():
        return {
            "fen": board.fen(),
            "status": "fim",
            "message": "Xeque-mate! Brancas venceram!",
            "player_move": move,
            "stockfish_move": None
        }

    stockfish.set_fen_position(board.fen())
    best_move = stockfish.get_best_move()

    if best_move and chess.Move.from_uci(best_move) in board.legal_moves:
        board.push(chess.Move.from_uci(best_move))
        stockfish.set_fen_position(board.fen())

        if board.is_checkmate():
            return {
                "fen": board.fen(),
                "status": "fim",
                "message": "Xeque-mate! Pretas venceram!",
                "player_move": move,
                "stockfish_move": best_move
            }

    return {
        "fen": board.fen(),
        "status": "ok",
        "player_move": move,
        "stockfish_move": best_move
    }

# ROTAS A SEREM USADAS AO PENSAR EM INTEGRAR COM O ROBO
@app.get("/get_position/{square}", tags=['ROBOT'])
def get_move_vector(move: str):
    """
    Recebe uma jogada como 'h2h3' e retorna o deslocamento em X, Y
    e o ângulo inteiro que o robô deve girar a partir da posição 0 (referência horizontal).
    """

    if len(move) != 4:
        raise HTTPException(status_code=400, detail="Jogada inválida! Use formato padrão, ex: 'h2h3'.")

    from_square = move[:2]
    to_square = move[2:]

    def get_position(square: str):
        if len(square) != 2 or square[0] not in "abcdefgh" or square[1] not in "12345678":
            raise HTTPException(status_code=400, detail=f"Posição inválida: {square}")

        column_map = {
            "a": 1000, "b": 2000, "c": 3000, "d": 4000,
            "e": 5000, "f": 6000, "g": 7000, "h": 8000
        }

        row_map = {
            "1": 1000, "2": 2000, "3": 3000, "4": 4000,
            "5": 5000, "6": 6000, "7": 7000, "8": 8000
        }

        x = column_map[square[0]]
        y = row_map[square[1]]
        return (x, y)

    # Posições de origem e destino
    x1, y1 = get_position(from_square)
    x2, y2 = get_position(to_square)

    # Vetor de deslocamento
    dx = x2 - x1
    dy = y2 - y1

    # Ângulo absoluto (em relação ao eixo X positivo) — referência 0°
    angle_rad = math.atan2(dy, dx)
    angle_deg = int(round(math.degrees(angle_rad)))

    # Corrige ângulos negativos para o intervalo 0°–359°
    if angle_deg < 0:
        angle_deg += 360

    return {
        "from": from_square,
        "to": to_square,
        "dx": dx,
        "dy": dy,
        "angle_deg": angle_deg  # usado sempre a partir da posição 0
    }

# Rotas de conexão DB
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/verify-token/", tags=['DB'])
def verify_token(token: str):
    try:
        payload = jwt.decode(token, str(SECRET_KEY), algorithms=[ALGORITHM])
        return {"valid": True, "user_id": payload["id"]}
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado.")
    except DecodeError:
        raise HTTPException(status_code=401, detail="Token inválido.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")

class CreateUserRequest(BaseModel):
    username: str
    password: str
    email: EmailStr

@app.post("/new-users/",tags=['DB'])
def create_user(user: CreateUserRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = bcrypt.hash(user.password)

    new_user = User(username=user.username, password=hashed_password, email=user.email)
    db.add(new_user)
    db.commit()

    return {
        "message": "User created successfully", 
        "id": new_user.id
    }

@app.get("/get-users/", tags=['DB'])
def get_users(db: Session = Depends(get_db)):
    users = db.query(User).order_by(desc(User.rating)).all()

    return [
        {
            "username": user.username,
            "rating": user.rating
        }
        for user in users
    ]

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/login/", tags=['DB'])
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not bcrypt.verify(payload.password, user.password):
        raise HTTPException(status_code=400, detail="Invalid username or password")

    expiration = datetime.utcnow() + timedelta(hours=48)
    token = jwt.encode({"id": user.id, "exp": expiration}, str(SECRET_KEY), algorithm=ALGORITHM)

    return {"message": "Login successful", "token": token, "user_id": user.id}

@app.get("/user-session/", tags=['DB'])
def get_user_info(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "wins": user.wins,
        "losses": user.losses,
        "total_games": user.total_games,
        "rating": user.rating,
    }

@app.get("/user-history/", tags=['DB'])
def get_user_history(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    games = db.query(Game).filter(Game.user_id == user.id).order_by(Game.id.desc()).all()

    resultado = []
    for game in games:
        first_move = (
            db.query(Move)
            .filter(Move.game_id == game.id)
            .order_by(Move.created_at.asc())
            .first()
        )
        last_move = (
            db.query(Move)
            .filter(Move.game_id == game.id)
            .order_by(Move.created_at.desc())
            .first()
        )

        if first_move and last_move:
            fmt = '%Y-%m-%d %H:%M:%S'  # ajuste conforme necessário
            first_dt = datetime.strptime(first_move.created_at, fmt)
            last_dt = datetime.strptime(last_move.created_at, fmt)

            duration = last_dt - first_dt
            total_seconds = int(duration.total_seconds())

            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60

            duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            duration_str = "00:00:00"

        resultado.append({
            "id": game.id,
            "username": f"{user.username}", 
            "result": "Derrota" if game.player_win == 2 else "Vitória",
            "duration": duration_str
        })

    return resultado

@app.get("/game-info/{game_id}", tags=["GAME"])
def get_game_info(game_id: int, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Jogo não encontrado")

    first_move = (
        db.query(Move)
        .filter(Move.game_id == game_id)
        .order_by(Move.created_at.asc())
        .first()
    )
    last_move = (
        db.query(Move)
        .filter(Move.game_id == game_id)
        .order_by(Move.created_at.desc())
        .first()
    )

    if first_move and last_move:
        fmt = "%Y-%m-%d %H:%M:%S"  # ou ajuste para o tipo real
        start = datetime.strptime(first_move.created_at, fmt)
        end = datetime.strptime(last_move.created_at, fmt)
        duration = end - start
        total_seconds = int(duration.total_seconds())
        minutes = total_seconds // 60
        duration_str = f"{minutes} minutos"
    else:
        duration_str = "Desconhecido"

    result = "Vitória" if game.player_win == 1 else "Derrota"

    return {"result": result, "duration": duration_str}


@app.post("/forgot-password/", tags=['DB'])
def forgot_password(email: str, db: Session = Depends(get_db)):
    """ Verifica se o e-mail existe e envia um link de recuperação """
    user = db.query(User).filter(User.email == email).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="E-mail não encontrado.")
    
    # Gerar token de redefinição de senha
    reset_token = create_reset_token(email)
    
    # Enviar e-mail com link
    send_reset_email(email, reset_token)
    
    return {"message": "E-mail de redefinição de senha enviado!"}
