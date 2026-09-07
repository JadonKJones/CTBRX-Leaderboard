import sqlite3
db = sqlite3.connect('instance/ctbrx.db')
res = db.execute("SELECT id, mods FROM score WHERE mods LIKE '%NC%' LIMIT 10;").fetchall()
print(res)
