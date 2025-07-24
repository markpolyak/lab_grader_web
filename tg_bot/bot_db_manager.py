import sqlite3
import os 

class Database:
    def __init__(self, db_file):
        self.db_file = db_file
        self.connection = sqlite3.connect(db_file, check_same_thread=False)
        self.cursor = self.connection.cursor()
        self._init_db()
    
    def _init_db(self):
        self.cursor.execute('''                            
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY NOT NULL,
            user_name TEXT,
            user_surname TEXT,
            user_patronim TEXT,
            user_github_nickname TEXT
        )
        ''')
        self.connection.commit()

    def close(self):
        self.cursor.close()
        self.connection.close()

    def add_user(self, user_id):
        self.cursor.execute("INSERT INTO users (id) VALUES (?)", (user_id,))
        self.connection.commit()

    def user_exist(self, user_id):
        self.cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        result = self.cursor.fetchall()
        return bool(len(result))
    
    



    def set_user_name(self, user_id, user_name):
        self.cursor.execute("UPDATE users SET user_name = ? WHERE id = ?", (user_name, user_id,))
        self.connection.commit()
    
    def set_user_surname(self, user_id, user_surname):
        self.cursor.execute("UPDATE users SET user_surname = ? WHERE id = ?", (user_surname, user_id,))
        self.connection.commit()

    def set_user_patronim(self, user_id, user_patronim):
        self.cursor.execute("UPDATE users SET user_patronim = ? WHERE id = ?", (user_patronim, user_id,))
        self.connection.commit()

    def set_user_github_nickname(self, user_id, user_github_nickname):
        self.cursor.execute("UPDATE users SET user_github_nickname = ? WHERE id = ?", (user_github_nickname, user_id,))
        self.connection.commit()


    
    def get_user_name(self, user_id):
        self.cursor.execute("SELECT user_name FROM users WHERE id = ?", (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None
    
    def get_user_surname(self, user_id):
        self.cursor.execute("SELECT user_surname FROM users WHERE id = ?", (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None

    def get_user_patronim(self, user_id):
        self.cursor.execute("SELECT user_patronim FROM users WHERE id = ?", (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None

    def get_user_github_nickname(self, user_id):
        self.cursor.execute("SELECT user_github_nickname FROM users WHERE id = ?", (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None
    

