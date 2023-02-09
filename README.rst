PrusaConnect SDK for Printer
=============================

:Requirements: basic knowledge of `PrusaConnect API docs <https://connect.prusa3d.com/docs/>`_.

Printer instance
----------------
You can create a Printer instance using the constructor and passing `server` and `token` to it. These you can find in  `prusa_printer_settings.ini`.

.. code:: python

    from prusa.connect.printer import Printer, const

    SERVER = "https://connect.prusa3d.com"
    SN = 'SERIAL_NUMBER_FROM_PRINTER'
    FINGERPRINT = 'Printer fingerprint'
    TOKEN = 'secret token from prusa_printer_settings.ini'
    printer = Printer(const.PrinterType.I3MK3, SN, SERVER, TOKEN)

    printer.loop()  # communication loop

Or you can use `Printer.from_config()` method which reads these values from the ini file.

.. code:: python

    from prusa.connect.printer import Printer, const

    SERVER = "https://connect.prusa3d.com"
    SN = 'SERIAL_NUMBER_FROM_PRINTER'
    printer = Printer.from_config("./prusa_printer_settings.ini",
                                  const.PrinterType.I3MK3, SN)

    printer.loop()  # communication loop


Registration
------------
If the printer has not been registered yet, you need to use `Printer.register()` to get a temporary code. This code is then used in the **Add Printer** form in Connect Web. After the printer
has been added to Connect, `Printer.get_token()` will return printer's persistent token.

.. code:: python

    from time import sleep
    from prusa.connect.printer import Printer, const

    SERVER = "https://connect.prusa3d.com"
    SN = 'SERIAL_NUMBER_FROM_PRINTER'
    printer = Printer(const.PrinterType.I3MK3, SN, SERVER)

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
Printer must send telemetry to connect at least each second. Because obtaining telemetry values might not be atomic, this must be done in a different thread than `Printer.loop`.

.. code:: python

    from threading import Thread
    from time import sleep

    ...

    # start communication loop
    thread = Thread(target=printer.loop)
    thread.start()

    # each second send telemetry to internal queue in the main-thread
    while True:
        printer.telemetry(const.State.READY, temp_nozzle=24.1, temp_bed=23.2)
        sleep(1)


Events
------
Events are a way to send information about the printer to Connect. They can be split into a few groups:

    * **Command answers** - Response for Connect if the command was be ACCEPTED,
      REJECTED, etc. These are handled by the SDK in `Printer.loop` method or in `Command.__call__` method.
    * **State change** - indicating that the printer state has changed. This are sent
      by `Printer.set_state` method.
    * **FILE INFO** events which are created by `FileSystem` object.
    * Alternatively you can inform Connect about other events like attaching/detaching of storage.
      You can do this by calling `Printer.event_cb`.

Examples for these groups follow below.

Event callback
--------------
You can inform Connect about some specific situation using events.

.. code:: python

    from threading import Thread

    ...

    # start communication loop
    thread = Thread(target=printer.loop)
    thread.start()

    try:
        ...
    except Exception as err:
        # send event to internal queue
        printer.event_cb(const.Event.ATTENTION, const.Source.WUI,
                         reason=str(err))

Printer state
-------------

.. code:: python

    from threading import Thread
    from time import sleep

    ...

    # start communication loop
    thread = Thread(target=printer.loop)
    thread.start()

    # toggle the state each second
    while True:
        if printer.state == const.State.READY:
            printer.set_state(const.State.BUSY, const.Source.MARLIN)
        elif printer.state == const.State.BUSY:
            printer.set_state(const.State.READY, const.Source.MARLIN)
        sleep(1)

Files
-----
Files are sent to Connect in a dictionary using the **SEND_INFO** command.
Within the **SEND_INFO** commmand response, there's a `files` dictionary with all
files and folders within the Filesystem. Here you can find info about file (or
folder). Available info is type, name, ro (read only), m_timestamp (when the
file was last modified), size and in case of folder, info about its children.
Also you can find here information about free_space and total_space of the each
storage, if available.

Commands
--------
When Connect sends a command in the answer to telemetry,
`Printer.command` object will be created. Please note that the `Printer.loop`
only creates and parametrizes this command instance. It never
calls this command's handler. It must happen in another (e.g. main) thread.

Each command handler must return a dictionary with at least the `source` key.

Normally each command is marked as finished by the FINISHED event. You
might want to override it by some other event, e.g. INFO. In that case,
also the `event` key must be set in the returned dictionary.

Additional data for this event is passed using the `data` key with
a dictionary as a value.

For further detail see https://connect.prusa3d.com/docs/printer_communication
or have a look at the implementation details in the SDK (INFO event
handled by the `Printer.get_info()` method).

.. code:: python

    from threading import Thread
    from time import sleep

    ...

    @printer.handler(const.Command.START_PRINT)
    def start_print(args: List[str]):
        """This handler will be called when START_PRINT command was sent to
           the printer."""
        printer.set_state(const.State.PRINTING, const.Source.CONNECT)
        print("Printing file: {args[0]}")
        ...

    @printer.handler(const.Command.STOP_PRINT)
    def start_print(args: List[str]):
        """This handler will be called when STOP_PRINT command was sent to
           the printer."""
        printer.set_state(const.State.READY, const.Source.CONNECT)
        print("Printing stopped")
        ...

    # communication loop
    thread = Thread(target=printer.loop)
    thread.start()

    # try run command handler each 100 ms
    while True:
        printer.command()
        sleep(0.1)
