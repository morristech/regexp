"""
A finite automaton is a finite-state machine that accepts or rejects
strings of symbols.
"""


from collections import defaultdict, OrderedDict
from contextlib import redirect_stdout
from io import StringIO
from functools import partial
from typing import Set

from .char import SIGMA, Character
from .nodes import Node, NDN, DN, trap_node
from .pattern import parse, expand


class FA:
    """Abstract Finite Automaton"""

    def __init__(self, initial_node: Node):
        """Create an automaton using the initial_node as entry point"""
        self.initial_node = initial_node

    @property
    def id(self) -> int:
        """Automaton unique identifier"""
        return self.initial_node.id

    @property
    def first_characters(self) -> Set[Character]:
        """Get the transition characters of the starting node"""
        return set(self.initial_node.transitions.keys())

    def match(self, string: str) -> bool:
        """Accept or reject the given string"""
        raise NotImplementedError("abstract method")

    def read_greedy(self, string: str) -> int:
        """
        Read the string as long as it matches
        :returns: #char read at last final node
        """
        raise NotImplementedError("abstract method")

    def read_lazy(self, string: str) -> int:
        """
        Read the string until it reads a final node
        :return: #char read at last final node
        """
        raise NotImplementedError("abstract method")

    def print_mesh(self) -> None:
        """Pretty print the current automaton"""
        buffer_ = StringIO()

        # Feed the buffer with the tree
        with redirect_stdout(buffer_):
            print(" " * len(str(Node.count - 1)), "-->", self.initial_node)
            seen = set()
            show = {self.initial_node}
            while show:
                next_nodes = set()
                for node in show:
                    node.print_transitions()
                    seen.add(node)
                    if isinstance(self, DFA):
                        next_nodes.update(node.transitions.values())
                    else:
                        next_nodes.update(*node.transitions.values())
                show = next_nodes - seen

        # Pretty print the tree by sorting nodes and
        # printing final nodes at the end
        lines = buffer_.getvalue().splitlines()
        ends = []
        for idx, line in enumerate(lines):
            if line.endswith("-->"):
                ends.append(line)
                lines[idx] = None
        for line in sorted(filter(bool, lines)):
            print(line)
        for line in sorted(ends):
            print(line)

    def __str__(self):
        return "<{} {} on {}>".format(self.__class__.__name__, self.id, self.initial_node)

class NFA(FA):
    """
    Non Deterministic Finite Automaton

    A NFA is made of :func:`Non Deterministic Nodes <regexp.nodes.NDA>`,
    they accept both void transition characters and same transition
    character targeting different nodes.

    There is a systematic mapping between :func:`~regexp.pattern`
    expression and a NFA but they are inefficient in term of matching.
    """

    def match(self, string: str) -> bool:
        new_nodes = {self.initial_node}
        self._expand(new_nodes)
        for char in string:
            old_nodes = new_nodes
            new_nodes = set()
            for node in old_nodes:
                new_nodes |= node.read(char)
                new_nodes |= node.read(SIGMA)
            self._expand(new_nodes)
            if not new_nodes:
                return False
        return any(map(lambda n: n.is_final, new_nodes))

    def read_greedy(self, string: str) -> int:
        raise NotImplementedError()

    def read_lazy(self, string: str) -> int:
        raise NotImplementedError()

    @staticmethod
    def _expand(nodes: Set[NDN]) -> None:
        """
        Update the current set of nodes to includes nodes reached by
        following void transitions on the current (+updated) nodes
        """
        new_nodes = nodes.copy()
        while True:
            next_nodes = set()
            for node in new_nodes:
                next_nodes.update(node.read(""))
            new_nodes = next_nodes - nodes
            if not new_nodes:
                break
            nodes.update(new_nodes)

    @classmethod
    def from_pattern(cls, pattern: str, flags: int) -> "NFA":
        """Create a NFA out of a regexp pattern"""
        return cls(parse(pattern, flags=0))

    @classmethod
    def from_extended_pattern(cls, pattern: str, flags: int) -> "NFA":
        return cls.from_pattern(expand(pattern), flags)


class DFA(FA):
    """
    Deterministic Finite Automaton

    A DFA is made of :func:`Deterministic Node <regexp.nodes.DN>`, they
    don't accept nor void transitions nor same transition character
    targeting different nodes.

    As they are deterministic, they provide an efficiant
    :func:`<regexp.automatons.DFA.match>` method.
    """

    @property
    def _dead_node(self):
        return None

    def match(self, string: str) -> bool:
        node = self.initial_node
        for letter in string:
            node = node.read(letter)
            if node is self._dead_node:
                return False
        return node.is_final

    def read_greedy(self, string: str) -> int:
        node = self.initial_node
        length = 0
        last_final_node = 0

        for letter in string:
            node = node.read(letter)
            if node is self._dead_node:
                break
            length += 1
            if node.is_final:
                last_final_node = length
        return last_final_node

    def read_lazy(self, string: str) -> int:
        node = self.initial_node
        length = 0
        for letter in string:
            node = node.read(letter)
            if node is self._dead_node:
                return 0
            length += 1
            if node.is_final:
                return length

    @classmethod
    def from_pattern(cls, pattern: str, flags: int) -> "DFA":
        nda = NFA.from_pattern(pattern, flags)
        return cls.from_ndfa(nda)

    @classmethod
    def from_ndfa(cls, nda: NFA) -> "DFA":
        """
        Determine a :func:`Non Deterministic Automaton
        <regexp.automatons.NFA>`.

        NFA determinization theorie is available on `wikipedia
        <https://en.wikipedia.org/wiki/Powerset_construction>`
        """

        # Pattern: a*b
        #                /<-ε--\
        # NFA: (0)--ε->(1)--a->(2)--ε->(3)--b->(4)->
        #        \----------ε--------->/
        #
        # Derivation table:    nodes  |    a    |  b
        #                    ---------+---------+-----
        #                     {0,1,3} | {1,2,3} | {4}
        #                     {1,2,3} | {1,2,3} | {4}
        #                       {4}   |         |
        #
        # ndn_to_dn: {{0,1,3}: {5}, {1,2,3}: {6}, {4}: {7}}
        #
        #             \<a-/
        # DA: (5)--a-->(6)--b-->(7)
        #       \-------b------>/

        initial_nodes = {nda.initial_node}
        nda._expand(initial_nodes)
        initial_nodes = frozenset(initial_nodes)

        stack = [initial_nodes]
        derivation_table = {}

        # Create and fill the derivation table
        while stack:
            cur_nodes = stack.pop()
            alphabet = set()
            for node in cur_nodes:
                alphabet.update(node.transitions.keys())
            alphabet.difference_update(set([""]))

            derivation_table[cur_nodes] = {}
            for char in alphabet:
                all_targets = set()
                for node in cur_nodes:
                    targets = node.read(char)
                    nda._expand(targets)
                    all_targets.update(targets)
                cell_nodes = frozenset(all_targets)
                if cell_nodes not in derivation_table:
                    stack.append(cell_nodes)
                derivation_table[cur_nodes][char] = cell_nodes

        # Create a new deterministic node for each group of
        # non-deterministic nodes from the derivation table
        ndn_to_dn = {}
        for nodes in derivation_table:
            is_final = any(map(lambda n: n.is_final, nodes))
            dn = DN(is_final)
            ndn_to_dn[nodes] = dn

        # Link deterministic nodes using the derivation table
        for nodes in derivation_table:
            dn = ndn_to_dn[nodes]
            for char in derivation_table[nodes]:
                dn.add(char, ndn_to_dn[derivation_table[nodes][char]])

        return cls(ndn_to_dn[initial_nodes])


class DCFA(DFA):
    """
    Deterministic Completed Finite Automaton

    A DCFA is a DFA whose all nodes have transitions for the entire
    alphabet.

    Such automatons are pretty useless by themself but facilitate  the
    creation of :func:`Minimalist Automatons <regexp.automatons.DCMFA>`
    and :func:`Inverted Automatons <regexp.automatons.DCIFA>`.
    """

    @property
    def _dead_node(self):
        return trap_node

    @classmethod
    def from_pattern(cls, pattern: str, flags: int) -> "DCFA":
        da = super().from_pattern(pattern, flags)
        return cls.from_dfa(da)

    @classmethod
    def from_ndfa(cls, nda: NFA) -> "DCFA":
        da = super().from_ndfa(nda)
        return cls.from_dfa(da)

    @classmethod
    def from_dfa(cls, da: DFA) -> "DCFA":
        """
        Complete a :func:`Deterministic Automaton <regexp.automatons.DFA>`

        Add a *catch all* transition targeting the
        :func:`trap node <regexp.nodes.trap_node>` on every node.
        """
        dca = cls(da.initial_node)
        seen = set()
        nodes = [dca.initial_node]
        while nodes:
            node = nodes.pop()
            new_nodes = set(node.transitions.values()) - seen
            nodes.extend(new_nodes)
            seen |= new_nodes
            node.transitions.setdefault(SIGMA, trap_node)
        return dca


class DCMFA(DCFA):
    """
    Deterministic Completed Minimalist Finite Automaton

    A DCMFA is the smallest (made of the least nodes) automaton capable
    of matching a given pattern. They are memory efficient.
    """

    @classmethod
    def from_pattern(cls, pattern: str, flags: int) -> "DCMFA":
        dca = super().from_pattern(pattern, flags)
        return cls.from_dcfa(dca)

    @classmethod
    def from_ndfa(cls, nda: NFA) -> "DCMFA":
        dca = super().from_ndfa(nda)
        return cls.from_dcfa(dca)

    @classmethod
    def from_dfa(cls, da: DFA) -> "DCMFA":
        dca = super().from_dfa(da)
        return cls.from_dcfa(dca)

    @classmethod
    def from_dcfa(cls, dca: DCFA) -> "DCMFA":
        """
        Minimize a :func:`Completed Automaton <regexp.automatons.DCFA>`

        DFA minimization theorie is available on `wikipedia
        <https://en.wikipedia.org/wiki/DFA_minimization>`_
        """

        # Gather automaton's nodes
        dca_nodes = set([dca.initial_node])
        new_nodes = set([dca.initial_node])
        while new_nodes:
            new_nodes_targets = set()
            for node in new_nodes:
                new_nodes_targets.update(set(node.transitions.values()))
            new_nodes = new_nodes_targets - dca_nodes
            dca_nodes.update(new_nodes)
        dca_nodes = sorted(list(dca_nodes), key=lambda n: n.id)

        # Gather automaton's alphabet
        # determinism (=order) is important and sigma must be the last
        alphabet = set()
        for node in dca_nodes:
            alphabet.update(node.transitions.keys())
        alphabet.remove(SIGMA)
        alphabet = sorted(list(alphabet)) + [SIGMA]

        # Create the minimal derivation table
        nodes = None
        new_nodes = OrderedDict((node, int(node.is_final) + 1) for node in dca_nodes)
        system = 3
        while nodes != new_nodes:
            nodes = new_nodes
            derivations = {}
            for node in nodes:
                derivations[node] = nodes[node]
                for rank, char in enumerate(alphabet):
                    target = node.transitions.get(char)
                    if target:
                        derivations[node] += nodes[target] * system ** (rank + 1)

            system = 1
            ids = {}
            new_nodes = OrderedDict()
            for node in nodes:
                if derivations[node] not in ids:
                    ids[derivations[node]] = system
                    system += 1
                new_nodes[node] = ids[derivations[node]]

        # Rename
        dca_to_id = nodes

        # Create nodes for new automaton
        id_to_dcma = {dca_to_id[node]: DN.duplicate(node) for node in dca_nodes}

        # Link DCMA nodes
        seen_ids = set()
        for dca_node in dca_nodes:
            dca_node_id = dca_to_id[dca_node]
            if dca_node_id not in seen_ids:
                seen_ids.add(dca_node_id)
                dcma_node = id_to_dcma[dca_node_id]
                for char, dca_target in dca_node.transitions.items():
                    dcma_target = id_to_dcma[dca_to_id[dca_target]]
                    dcma_node.add(char, dcma_target)

        return cls(id_to_dcma[dca_to_id[dca.initial_node]])


class DCIFA(DCFA):
    """
    Deterministic Completed Inverted Finite Automaton

    Provides a :func:`<regexp.automatons.DCIFA.match>` method that
    negate the result of a normal match.
    """

    def match(self, string: str) -> bool:
        return not super().match(string)


FiniteAutomaton = FA
NonDeterministicFiniteAutomaton = NFA
DeterministicFiniteAutomaton = DFA
DeterministicCompletedFiniteAutomaton = DCFA
DeterministicCompletedMinimalistFiniteAutomaton = DCMFA
DeterministicCompletedInvertedFiniteAutomaton = DCIFA
