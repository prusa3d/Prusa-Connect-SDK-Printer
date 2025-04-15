PrusaConnect SDK for Printer
=============================

Printer instance
----------------
You can create a Printer instance using the constructor passing printer type,
serial number and printer fingerprint to it. Serial number can be found on the printer label.
For I3MK3 and SL1, printer fingerprint is a SHA256 HEX digest of the serial number.

.. code:: python

    from prusa.connect.printer import Printer, const

    SN = "Printer serial number"
    FINGERPRINT = sha256(SN.encode()).hexdigest()
    PRINTER_TYPE = const.PrinterType.I3MK3
    printer = Printer(PRINTER_TYPE, SN, FINGERPRINT)

For setting the connection you should call `set_connection` passing `server` and `token` to it. These you can find in the `prusa_printer_settings.ini` file. You can download it after printer registration from the Connect web by selecting your printer -> Settings -> LAN settings -> Download settings. If the printer has not been registered yet, refer to the Registration section below.

.. code:: python

    SERVER = "https://connect.prusa3d.com"
    TOKEN = "Secret token from prusa_printer_settings.ini"

    printer.set_connection(SERVER, TOKEN)

    printer.loop()  # Communication loop

Or you can use `printer.connection_from_config()` method which reads these values directly from the .ini file.

.. code:: python

    printer.connection_from_config("./prusa_printer_settings.ini")

    printer.loop()  # Communication loop


Registration
------------
If the printer has not been registered yet, you need to use `Printer.register()` to get a temporary code. Enter this code in the **Add Printer** form in Connect Web. After the printer has been added to Connect, your Printer instance will automatically retrieve the code in the background.

.. code:: python

    from time import sleep
    from prusa.connect.printer import Printer, const

    SERVER = "https://connect.prusa3d.com"
    SN = "Printer serial number"
    FINGERPRINT = "Printer fingerprint"
    PRINTER_TYPE = const.PrinterType.I3MK3
    printer = Printer(PRINTER_TYPE, SN, FINGERPRINT)

    printer.set_connection(SERVER, None)

    tmp_code = printer.register()
    print(f"Use this code `{tmp_code}` in add printer form "
          f"{SERVER}/printers/overview?code={tmp_code}.")

    while printer.token is None:
        print("Waiting for the printer registration on the Connect web...")
        sleep(1)

    print(f"Printer is registered with token {printer.token}")

Note: For I3MK3 the Add Printer form does not allow you manually enter the temporary code.
You can use this url to add it directly: https://connect.prusa3d.com:443/add-printer/connect/{PRINTER_TYPE}/{tmp_code}

Telemetry
---------
Printer must send telemetry to Connect at least once per second. Because obtaining telemetry values might not be atomic, this must be done in a separate thread from `Printer.loop`.

.. code:: python

    from threading import Thread
    from time import sleep

    ...

    # Start communication loop in a separate thread
    thread = Thread(target=printer.loop)
    thread.start()

    # Send telemetry to the main thread queue once per second
    while True:
        printer.telemetry(const.State.READY, temp_nozzle=24.1, temp_bed=23.2)
        sleep(1)

Events
------
Events are a way to send information about the printer to Connect.
They can be grouped into several categories:

- **Command answers**
    Responses to Connect indicating whether a command was ACCEPTED, REJECTED, etc.
    These are handled by the SDK in the `Printer.loop` method or the `Command.__call__` method.
- **State changes**
    Indicate that the printer's state has changed. These are sent by the `Printer.set_state` method.
- **FILE INFO**
    Events created by the FileSystem object.
- **Other events**
    For example, informing Connect about storage being attached or detached.
    You can do this by calling `Printer.event_cb`.

Examples of each category are provided below.

Event callback
--------------
You can inform Connect about some specific situation using events.

.. code:: python

    from threading import Thread

    ...

    # Start communication loop
    thread = Thread(target=printer.loop)
    thread.start()

    try:
        ...
    except Exception as err:
        # Send event to internal queue
        printer.event_cb(const.Event.FAILED, const.Source.WUI,
                         reason=str(err))

Printer state
-------------

.. code:: python

    from threading import Thread
    from time import sleep

    ...

    # Start communication loop
    thread = Thread(target=printer.loop)
    thread.start()

    # Toggle the state each second
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

.. code:: python

    from threading import Thread
    from time import sleep

    ...

    @printer.handler(const.Command.START_PRINT)
    def start_print(args: list[str]):
        """This handler will be called when START_PRINT command was sent to
           the printer."""
        printer.set_state(const.State.PRINTING, const.Source.CONNECT)
        print("Printing file: {args[0]}")
        ...

    @printer.handler(const.Command.STOP_PRINT)
    def start_print(args: list[str]):
        """This handler will be called when STOP_PRINT command was sent to
           the printer."""
        printer.set_state(const.State.READY, const.Source.CONNECT)
        print("Printing stopped")
        ...

    # Communication loop
    thread = Thread(target=printer.loop)
    thread.start()

    # Set printer state to READY.
    printer.set_state(const.State.READY, const.Source.CONNECT)

    # Try run command handler each 100 ms
    while True:
        printer.command()
        sleep(0.1)
