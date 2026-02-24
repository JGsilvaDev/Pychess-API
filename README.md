# ♟️ PyChess API

Backend REST API built with FastAPI for chess game management and automated move analysis through Stockfish engine integration.

This project focuses on structured architecture, business rule validation and scalable endpoint design, simulating a real-world corporate backend scenario.

---

## 🏗 Architecture Overview

The API follows a modular architecture with clear separation of responsibilities:

- **Routers Layer** – Defines HTTP endpoints and request handling
- **Service Layer** – Contains business rules and move validation logic
- **Engine Integration Layer** – Responsible for communication with the Stockfish engine
- **Schemas/Models** – Data validation and serialization using Pydantic
- **Persistence Layer** – Manages game state and data consistency

This structure improves maintainability, scalability and testability.

---

## 🔍 Core Features

- Chess move validation
- Automated move analysis using Stockfish
- Game state management
- Structured request validation with Pydantic
- Consistent error handling
- Interactive API documentation via Swagger

---

## 🔌 Stockfish Engine Integration

The API integrates directly with the Stockfish chess engine to:

- Evaluate board positions
- Classify move quality
- Suggest optimal moves

The engine communication layer is abstracted from the API layer to maintain separation of concerns and allow future extensibility.

---

## ⚙️ Tech Stack

- Python
- FastAPI
- Uvicorn
- Pydantic
- Stockfish Engine
- RESTful Architecture

---

## 🚀 Running Locally

### 1. Create virtual environment

```bash
python -m venv venv
```

### 2. Activate virtual environment  
- **Windows**:  
  ```
  venv\Scripts\activate
  ```
- **Linux/Mac**:  
  ```
  source venv/bin/activate
  ```

### 3. Install dependencies 
```
pip install fastapi uvicorn
```

### 4. Start the server 
```
uvicorn main:app --reload
```

## API Documentation 

After running the server, access the interactive documentation:
🔗 **[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)**

---

📌 **Design Considerations**  
- Modular and scalable structure
- Clear separation between API layer and engine logic
- Centralized business rule handling
- Data validation using Pydantic schemas
- Structured error responses
- Designed to support future authentication layer (JWT / RBAC)

📈 Planned Improvements
- JWT Authentication
- CI/CD pipeline setup
- Deployment configuration

🎯 Purpose of the Project
- This project was designed to demonstrate:
- Backend architecture structuring
- External engine integration
- Business rule abstraction
- Clean and maintainable API design
- RESTful service organization

It simulates a production-ready backend scenario focused on scalability and maintainability.

🤝 Contributions
Contributions, improvements and architectural discussions are welcome.
Feel free to open issues or submit pull requests.

