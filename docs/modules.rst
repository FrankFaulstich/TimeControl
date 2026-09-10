Application Modules
===================

This section provides detailed documentation for all modules within the
TimeControl application, generated automatically from the source code's
docstrings.

Every module the application is built from appears below.
``tests/test_documentation.py`` keeps that true in both directions: a module
added to the repository and not listed here fails the test suite, and so does
a name listed here that no longer resolves to a module. Sphinx only *warns*
about the second case and carries on, which is how this page once lost its
largest chapter without anybody noticing.

Core Logic
----------

TimeTracker
~~~~~~~~~~~

.. automodule:: tt.TimeTracker
   :members:

File Locking
~~~~~~~~~~~~

.. automodule:: tt.filelock
   :members:

Task Presentation
-----------------

Small, self-contained modules behind the three tabs of the interface. They
hold no state and touch no files, which is what makes them testable on their
own.

Ordering
~~~~~~~~

.. automodule:: tt.task_order
   :members:

Progress
~~~~~~~~

.. automodule:: tt.task_progress
   :members:

Calendar
~~~~~~~~

.. automodule:: tt.task_calendar
   :members:

Synchronisation
---------------

Sync Client
~~~~~~~~~~~

.. automodule:: tt.sync_client
   :members:

Sync Engine
~~~~~~~~~~~

.. automodule:: tt.sync_engine
   :members:

Outgoing Queue
~~~~~~~~~~~~~~

.. automodule:: tt.sync_outbox
   :members:

Applying Remote Operations
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: tt.sync_apply
   :members:

Sync Log
~~~~~~~~

.. automodule:: tt.sync_log
   :members:

Error Messages
~~~~~~~~~~~~~~

.. automodule:: tt.sync_messages
   :members:

Interfaces
----------

The four ways into the same data. The Streamlit interface is the one people
use; the other three are for other programs.

Graphical Interface
~~~~~~~~~~~~~~~~~~~

.. automodule:: TimeTrackerSL_GUI
   :members:

MCP Server
~~~~~~~~~~

.. automodule:: TimeTrackerMCP_Server
   :members:

REST Server
~~~~~~~~~~~

.. automodule:: TimeTrackerREST_Server
   :members:

SOAP Server
~~~~~~~~~~~

.. automodule:: TimeTrackerSOAP_Server
   :members:

Installation and Updates
------------------------

Installer
~~~~~~~~~

.. automodule:: install
   :members:

Update Mechanism
~~~~~~~~~~~~~~~~

.. automodule:: update
   :members:
