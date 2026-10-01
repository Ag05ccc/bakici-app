"""Fault integration: actual HTTP, independent agents, synthetic radio data."""

import asyncio
import json
import threading
import time
import unittest

from agent import Agent, parse_config
from registry import Registry
from web_app import ScannerHTTPServer


def server_config():
    return {"nodes": [
        {"id": f"pi-0{n}", "label": f"Pi {n}", "floor": n, "location": "Test",
         "x": 50, "y": 50, "api_key": f"system-test-key-{n:02}"} for n in (1, 2)
    ]}


async def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("System did not reach the expected state")
        await asyncio.sleep(.01)


class SystemFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 0
        self.server = None
        self.thread = None
        self.running_agents = []
        self.agents = []
        self.start_server()

    def start_server(self, port=0):
        self.registry = Registry(server_config(), clock=lambda: self.now)
        self.server = ScannerHTTPServer(("127.0.0.1", port), registry=self.registry)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()

    def stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            self.server = None

    def start_agent(self, number, scanners):
        config = parse_config({
            "node_id": f"pi-0{number}", "api_key": f"system-test-key-{number:02}",
            "server_url": f"http://127.0.0.1:{self.server.server_port}",
            "bluetooth": {"timeout": .1, "pause": .03},
            "wifi": {"timeout": .1, "interval": .04}, "heartbeat_interval": .03,
        })
        stop = asyncio.Event()
        agent = Agent(config, scanners=scanners)
        self.agents.append(agent)
        task = asyncio.create_task(agent.run(stop))
        self.running_agents.append((stop, task))
        return stop, task

    async def asyncTearDown(self):
        for stop, task in self.running_agents:
            stop.set()
        for stop, task in self.running_agents:
            await asyncio.wait_for(task, 3)
        self.stop_server()

    def node(self, number):
        return self.registry.snapshot()["nodes"][number - 1]

    async def test_radio_fault_and_agent_stop_leave_other_node_working(self):
        async def working(*args, **kwargs):
            return [{"path": "/synthetic/1", "properties": {"Name": "Synthetic sensor"}}]

        async def failed(*args, **kwargs):
            raise RuntimeError("Simulated radio failure")

        first_stop, first_task = self.start_agent(1, {"bluetooth": working, "wifi": failed})
        self.start_agent(2, {"bluetooth": working, "wifi": working})
        await eventually(lambda: self.node(1)["radios"]["wifi"]["state"] == "error"
                         and self.node(2)["radios"]["wifi"]["state"] == "fresh"
                         and self.node(1)["heartbeat_age"] is not None)
        self.assertEqual(self.node(1)["radios"]["bluetooth"]["count"], 1)
        first_stop.set()
        await asyncio.wait_for(first_task, 3)
        self.now = 60
        await eventually(lambda: self.node(2)["age"] == 0)
        self.assertEqual(self.node(1)["state"], "offline")
        self.assertEqual(self.node(2)["state"], "online")
        self.assertEqual(self.node(2)["radios"]["wifi"]["count"], 1)

    async def test_server_outage_restart_discards_old_results_and_recovers(self):
        sequences = {"bluetooth": 0, "wifi": 0}
        scan_allowed = asyncio.Event()
        scan_allowed.set()

        def scanner(radio):
            async def scan(*args, **kwargs):
                await scan_allowed.wait()
                sequences[radio] += 1
                return [{"path": f"/{radio}/sensor", "properties": {"Sequence": sequences[radio]}}]
            return scan

        self.start_agent(1, {radio: scanner(radio) for radio in sequences})
        await eventually(lambda: self.node(1)["radios"]["bluetooth"]["count"] == 1
                         and self.node(1)["radios"]["wifi"]["count"] == 1)
        port = self.server.server_port
        before = dict(sequences)
        self.stop_server()
        with self.assertLogs("radio-agent", level="WARNING"):
            await eventually(lambda: all(sequences[r] >= before[r] + 3 for r in sequences))
            # Freeze producers and drain failed requests before defining the
            # restart boundary. A just-completed scan racing with listen() is
            # a valid in-flight report, not evidence of an offline replay queue.
            scan_allowed.clear()
            await eventually(lambda: all(not self.agents[0].sender.workers[r].is_alive() for r in sequences))
        at_restart = dict(sequences)
        self.start_server(port)
        self.assertEqual(self.node(1)["state"], "waiting")
        self.assertEqual(self.node(1)["radios"]["bluetooth"]["observations"], [])
        scan_allowed.set()
        await eventually(lambda: all(self.node(1)["radios"][r]["state"] == "fresh" for r in sequences))
        for radio in sequences:
            self.assertGreater(self.node(1)["radios"][radio]["observations"][0]["properties"]["Sequence"], at_restart[radio])

    async def test_heartbeat_after_restart_never_restores_observations(self):
        # Process restarts create a new registry from config, not persisted data.
        self.registry.accept({"node_id": "pi-01", "kind": "scan", "radio": "bluetooth",
                              "status": "success", "observations": [{"path": "/old", "properties": {}}]},
                             "system-test-key-01", "127.0.0.1")
        self.stop_server()
        self.start_server()

        async def pending(timeout, stop_event, **kwargs):
            await stop_event.wait()
            return []

        self.start_agent(1, {"bluetooth": pending, "wifi": pending})
        await eventually(lambda: self.node(1)["heartbeat_age"] is not None)
        self.assertEqual(self.node(1)["state"], "online")
        for state in self.node(1)["radios"].values():
            self.assertEqual(state["state"], "waiting")
            self.assertIsNone(state["count"])
            self.assertEqual(state["observations"], [])
        self.assertNotIn("system-test-key", json.dumps(self.registry.snapshot()))


if __name__ == "__main__":
    unittest.main()
