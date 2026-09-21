from .db import make_pool, migrate

if __name__ == '__main__':
    with make_pool() as pool:
        pool.wait()
        migrate(pool)
    print('Catálogo SARA actualizado.')
