Prusa Connect SDK for Printer
=============================

:Requirements: Base Prusa Connect API knowledge https://connect.prusa3d.com/doc

Printer instance
----------------
You can create Printer instance with constructor, so you must know `server`,
and `token`, which you can read them from lan_settings.ini.

.. code:: python

    from prusa.connect.printer import Printer, const

    SERVER = "https://connect.prusa3d.com"
    SN = 'SERIAL_NUMBER_FROM_PRINTER'
    TOKEN = 'secret token from lan_settings.ini'
    printer = Printer(const.Printer.I3MK3, SN, SERVER, TOKEN)

    printer.loop()  # communication loop

Or you can use
Printer.from_config method, which know read these values from ini file.

.. code:: python

    from prusa.connect.printer import Printer, const

    SERVER = "https://connect.prusa3d.com"
    SN = 'SERIAL_NUMBER_FROM_PRINTER'
    printer = Printer.from_config("./lan_settings_ini", const.Printer.I3MK3, SN)

    printer.loop()  # communication loop


Registration
------------
When printer is not registered, you must use Printer.register method to
get temporary code. User must use this code in *Add Printer* form. When printer
was added to Connect, Printer.get_token method returned persistent token.

.. code:: python

    from time import sleep
    from prusa.connect.printer import Printer, const

    SERVER = "https://connect.prusa3d.com"
    SN = 'SERIAL_NUMBER_FROM_PRINTER'
    printer = Printer(const.Printer.I3MK3, SN, SERVER)

    tmp_code = printer.register()
    print(f"Use this code `{tmp_code}` in add printer form "
          f"{SERVER}/printers/overview?code={tmp_code}.")

    token = None
    while token is None:
        token = printer.get_token(tmp_code)
        sleep(1)

    print("Printer is registered with token %s" % token)

Telemetry
---------
Printer must send telemetry each second. While getting telemetry values
can't be atomic, this must be do in another thread than Printer.loop.

.. code:: python

    from threading import Thread
    from time import sleep

    ...

    # start communication loop in another thread
    thread = Thread(target=printer.loop)
    thread.start()

    while True:  # send telemetry each second to internal queue
        printer.telemetry(const.State.READY, temp_nozzle=24.1, temp_bed=23.2)
        sleep(1)


Events
------
Events are way, to send information about printer to Connect. There can be
split to few groups:

    * Command answers - As respond to connect, if command was be ACCEPTED,
      REJECTED, etc. These answers are handled as events by SDK in
      Printer.loop method, or in Command.__call__ method.
    * State change - when Printer state was changed. This events will be
      send by Printer.set_state method.
    * FILE INFO events, which are create in FileSystem object.
    * Or you can inform Connect about other events like (un)mounting storage.
      You can this do by call Printer.event_cb.

Event callback
--------------
You can inform Connect on some specific situation, with another events.

.. code:: python

    from threading import Thread

    ...

    # start communication loop in another thread
    thread = Thread(target=printer.loop)
    thread.start()

    try:
        ...
    except Excpetion as err:
        # send event to internal queue
        printer.event_cb(const.Event.ATTENTION, const.Source.WUI, reason=str(err))

Printer state
-------------

.. code:: python

    from threading import Thread
    from time import sleep

    ...

    # start communication loop in another thread
    thread = Thread(target=printer.loop)
    thread.start()

    # switch state each second
    while True:
        if printer.state == const.State.READY:
            printer.set_state(const.State.BUSY, const.Source.MARLIN)
        elif printer.state == const.State.BUSY:
            printer.set_state(const.State.READY, const.Source.MARLIN)
        sleep(1)

Files
-----
**TODO**

Commands
--------
When Connect sends *command* as answer to telemetry, Printer.command object
will be set. But Printer.loop only set arguments to command, but never call
command handler. This must be happen in another (main) thread.

Each command handler must returned dictionary with `source` key. When command
can emit another event then FINISHED, `event` key must be set. Other arguments
will be send in `data` structure.

.. code:: python

    from threading import Thread
    from time import sleep

    ...

    @printer.handler(const.Command.START_PRINT)
    def start_print(args: List[str]):
        """This handler will be called when START_PRINT command was sent to
           the printer."""
        printer.set_state(const.State.PRINTING, const.Source.CONNECT)
        ...

    @printer.handler(const.Command.STOP_PRINT)
    def start_print(args: List[str]):
        """This handler will be called when STOP_PRINT command was sent to
           the printer."""
        printer.set_state(const.State.READY, const.Source.CONNECT)
        ...

    # start communication loop in another thread
    thread = Thread(target=printer.loop)
    thread.start()

    # try run command handler each 100 ms
    while True:
        printer.command()
        sleep(0.1)
