#!/usr/bin/env python3
"""Скрипт для додавання балансу користувачу (для тестування)."""
import sys
from database import SessionLocal, init_db
from database.models import User, TonTransaction
from datetime import datetime

def add_balance(user_id: int, amount: float, comment: str = "test_balance_add"):
    """Додати баланс користувачу."""
    # Спочатку виконати всі міграції
    print("Running database migrations...")
    init_db()
    print("Migrations completed.")
    
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            # Створити користувача, якщо не існує
            user = User(tg_id=user_id, balance=0, balance_usdt=0.0)
            db.add(user)
            db.flush()
        
        # Переконатися, що balance_usdt існує
        if getattr(user, "balance_usdt", None) is None:
            user.balance_usdt = 0.0
        
        old_balance = user.balance_usdt
        user.balance_usdt = round(user.balance_usdt + amount, 6)
        
        # Додати транзакцію для історії
        tx_id = f"test_{comment}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        db.add(TonTransaction(
            tx_hash=tx_id,
            user_id=user_id,
            amount=amount,
            comment=f"admin_add_balance_{comment}"
        ))
        
        db.commit()
        print(f"OK: Balance added!")
        print(f"   User: {user_id}")
        print(f"   Old balance: {old_balance} USDT")
        print(f"   Added: {amount} USDT")
        print(f"   New balance: {user.balance_usdt} USDT")
        return True
    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Використання: python add_balance.py <user_id> <amount>")
        print("Приклад: python add_balance.py 7717041708 1000")
        sys.exit(1)
    
    user_id = int(sys.argv[1])
    amount = float(sys.argv[2])
    
    add_balance(user_id, amount)
