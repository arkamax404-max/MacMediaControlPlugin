import asyncio
import logging
import signal
import sys
import threading

from .paths import CompanionPaths

create_media_adapter = artwork_processor = create_server = MediaStateCache = None
configure_logging = ensure_token = load_token = None
create_diagnostics = None
create_audio_controller = None
create_lock = None
STARTUP_MISSING_MODULE = "companion_start_failed category=missing_module"
STARTUP_INITIALIZATION_FAILURE = "companion_start_failed category=initialization_failure"


def CompanionLifecycle(*args, **kwargs):
    from .lifecycle import CompanionLifecycle as implementation
    return implementation(*args, **kwargs)


def shutdown_signals():
    signals = [signal.SIGINT]
    for name in ("SIGBREAK", "SIGTERM"):
        value = getattr(signal, name, None)
        if value is not None and value not in signals:
            signals.append(value)
    return signals


def install_signal_handlers(loop, stop_event):
    previous_handlers = {}

    def notify_shutdown(_signal_number, _frame):
        loop.call_soon_threadsafe(stop_event.set)

    for signal_name in shutdown_signals():
        try:
            previous_handlers[signal_name] = signal.signal(
                signal_name, notify_shutdown
            )
        except (OSError, ValueError):
            pass
    return previous_handlers


def restore_signal_handlers(previous_handlers):
    for signal_name, previous_handler in previous_handlers.items():
        try:
            signal.signal(signal_name, previous_handler)
        except (OSError, ValueError):
            pass


async def run_bridge(token, lifecycle=None):
    load_bridge_dependencies()
    loop = asyncio.get_running_loop()
    lifecycle = lifecycle or CompanionLifecycle()
    stop_event = asyncio.Event()
    previous_handlers = install_signal_handlers(loop, stop_event)
    adapter = audio = server = server_thread = refresh_task = None
    server_started = False
    try:
        cache = MediaStateCache()
        adapter = create_media_adapter(cache)
        await adapter.start()
        audio = create_audio_controller(cache)
        await asyncio.to_thread(audio.refresh)
        server = create_server(
            cache, adapter.command, loop, audio_commander=audio.command,
            artwork_lookup=artwork_processor.get_cached, token=token, lifecycle=lifecycle,
            request_stop=lambda: loop.call_soon_threadsafe(stop_event.set),
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        server_started = True

        def update_readiness():
            if lifecycle.status != "stopping":
                state = cache.get()
                lifecycle.set_status("ready" if state.available or state.audio_available
                                     else "degraded")

        async def refresh_periodically():
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=5)
                except TimeoutError:
                    await asyncio.gather(adapter.refresh(), asyncio.to_thread(audio.refresh))
                    update_readiness()

        update_readiness()
        logging.getLogger("d200_bridge").info("companion_listening")
        refresh_task = asyncio.create_task(refresh_periodically())
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        lifecycle.set_status("stopping")
        stop_event.set()
        cleanup_errors = []
        async def safely_await(awaitable):
            try:
                await awaitable
            except BaseException as error:
                cleanup_errors.append(error)
        def safely(operation):
            try:
                operation()
            except BaseException as error:
                cleanup_errors.append(error)
        if refresh_task:
            await safely_await(refresh_task)
        for operation in (server.shutdown if server_started else None,
                          server.server_close if server else None):
            if operation:
                safely(operation)
        if adapter:
            await safely_await(adapter.stop())
        if audio:
            safely(audio.stop)
        if server_started:
            safely(lambda: server_thread.join(timeout=2))
        safely(lambda: restore_signal_handlers(previous_handlers))
        if cleanup_errors and sys.exc_info()[0] is None:
            raise cleanup_errors[0]


def load_bridge_dependencies():
    global create_media_adapter, artwork_processor, create_server, MediaStateCache, create_audio_controller
    if create_media_adapter is None:
        from .media_adapter import create_media_adapter as _value
        create_media_adapter = _value
    if artwork_processor is None:
        from .artwork import artwork_processor as _value
        artwork_processor = _value
    if create_audio_controller is None:
        from .platform_services import create_audio_controller as _value
        create_audio_controller = _value
    if create_server is None:
        from .server import create_server as _value
        create_server = _value
    if MediaStateCache is None:
        from .state import MediaStateCache as _value
        MediaStateCache = _value
    from .artwork import Image
    if Image is None:
        raise ModuleNotFoundError(name="PIL")


def startup_failure_marker(error):
    if isinstance(error, ModuleNotFoundError):
        return STARTUP_MISSING_MODULE
    return STARTUP_INITIALIZATION_FAILURE


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--diagnose"]:
        global create_diagnostics
        try:
            if create_diagnostics is None:
                from .diagnostics import create_diagnostics as _value
                create_diagnostics = _value
            destination = create_diagnostics()
            print(f"Diagnostics created: {destination}")
            return 0
        except (OSError, RuntimeError, ValueError):
            print("Diagnostics could not be created")
            return 1
    if arguments == ["--preflight"]:
        try:
            load_bridge_dependencies()
            print("companion_preflight_ready")
            return 0
        except Exception as error:
            print(startup_failure_marker(error), file=sys.stderr)
            return 1
    if arguments:
        return 2
    global ensure_token, configure_logging, create_lock
    if ensure_token is None:
        from .paths import ensure_token as _token
        from .logging_config import configure_logging as _logging
        ensure_token, configure_logging = _token, _logging
    if create_lock is None:
        from .platform_services import create_lock as _lock
        create_lock = _lock
    mutex = None
    try:
        paths = CompanionPaths.from_environment()
        mutex = create_lock(paths)
        if not mutex.acquire():
            return 1 if mutex.unavailable else 0
        token = ensure_token(paths.token)
        configure_logging(paths.logs, token=token, console=True)
        asyncio.run(run_bridge(token=token))
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        logging.getLogger("d200_bridge").error(startup_failure_marker(error))
        return 1
    finally:
        if mutex is not None:
            mutex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
