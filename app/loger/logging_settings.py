logging_config = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            'format': '%(filename)s:%(lineno)d #%(levelname)-8s '
            '[%(asctime)s] -%(name)s - %(message)s'
        }
    },
    'handlers': {
        'default': {
            'class': 'logging.FileHandler',
            'filename': './app/logs/logs.log',
            'mode': 'a',
            'level': 'INFO',
            'formatter': 'default',
            'encoding': 'utf-8'
        }
    },
    'loggers': {
        'httpx': {
            'level': 'WARNING',
            'handlers': [],
            'propagate': False
        },
        'httpcore': {
            'level': 'WARNING',
            'handlers': [],
            'propagate': False
        },
        'aiosqlite': {
            'level': 'WARNING',
            'handlers': [],
            'propagate': False
        },
        'uvicorn': {
            'level': 'INFO',
            'handlers': ['default'],
        },
        'uvicorn.access': {
            'level': 'WARNING',
            'handlers': [],
            'propagate': False
        },
        'uvicorn.error': {
            'level': 'WARNING',
            'handlers': [],
            'propagate': False
        },
    },
    'root': {
        'formatter': 'default',
        'handlers': ['default'],
        'level': 'INFO'
    }
}
