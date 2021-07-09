# Copyright (c)  2020  Xiaomi Corp.       (author: Fangjun Kuang)

from functools import lru_cache
from typing import Iterable
from typing import List

import torch
import k2

from snowfall.common import get_phone_symbols


def build_ctc_topo(tokens: List[int]) -> k2.Fsa:
    '''Build CTC topology.
    A token which appears once on the right side (i.e. olabels) may
    appear multiple times on the left side (ilabels), possibly with
    epsilons in between.
    When 0 appears on the left side, it represents the blank symbol;
    when it appears on the right side, it indicates an epsilon. That
    is, 0 has two meanings here.
    Args:
      tokens:
        A list of tokens, e.g., phones, characters, etc.
    Returns:
      Returns an FST that converts repeated tokens to a single token.
    '''
    assert 0 in tokens, 'We assume 0 is ID of the blank symbol'

    num_states = len(tokens)
    final_state = num_states
    arcs = ''
    for i in range(num_states):
        for j in range(num_states):
            if i == j:
                arcs += f'{i} {i} {tokens[i]} 0 0.0\n'
            else:
                arcs += f'{i} {j} {tokens[j]} {tokens[j]} 0.0\n'
        arcs += f'{i} {final_state} -1 -1 0.0\n'
    arcs += f'{final_state}'
    ans = k2.Fsa.from_str(arcs, num_aux_labels=1)
    ans = k2.arc_sort(ans)
    return ans


def build_ctc_topo2(phones: List[int]):
    # See https://github.com/k2-fsa/k2/issues/746#issuecomment-856421616
    assert 0 in phones, 'We assume 0 is the ID of the blank symbol'
    phones = phones.copy()
    phones.remove(0)

    num_phones = len(phones)

    start = 0
    final = num_phones + 1

    arcs = []
    arcs.append([start, start, 0, 0, 0])
    arcs.append([start, final, -1, -1, 0])
    arcs.append([final])
    for i, p in enumerate(phones):
        i += 1
        arcs.append([start, start, p, p, 0])

        arcs.append([start, i, p, p, 0])
        arcs.append([i, i, p, 0, 0])

        arcs.append([i, start, p, 0, 0])

    arcs = sorted(arcs, key=lambda arc: arc[0])
    arcs = [[str(i) for i in arc] for arc in arcs]
    arcs = [' '.join(arc) for arc in arcs]
    arcs = '\n'.join(arcs)
    ctc_topo = k2.Fsa.from_str(arcs, False)
    return k2.arc_sort(ctc_topo)


class CtcTrainingGraphCompiler(object):

    def __init__(self,
                 L_inv: k2.Fsa,
                 phones: k2.SymbolTable,
                 words: k2.SymbolTable,
                 topo: str = 'modified',
                 oov: str = '<UNK>'):
        '''
        Args:
          L_inv:
            Its labels are words, while its aux_labels are phones.
        phones:
          The phone symbol table.
        words:
          The word symbol table.
        oov:
          Out of vocabulary word.
        '''
        if L_inv.properties & k2.fsa_properties.ARC_SORTED != 0:
            L_inv = k2.arc_sort(L_inv)

        assert oov in words

        self.L_inv = L_inv
        self.phones = phones
        self.words = words
        self.oov = oov
        phone_ids = get_phone_symbols(phones)
        phone_ids_with_blank = [0] + phone_ids
        if topo == 'normal':
            self.ctc_topo = k2.arc_sort(build_ctc_topo(phone_ids_with_blank))
        else:
            self.ctc_topo = k2.arc_sort(build_ctc_topo2(phone_ids_with_blank))


    def words() -> k2.SymbolTable:
        return self.words


    def oov() -> str:
        return self.oov


    def compile(self, texts: Iterable[str]) ->k2.Fsa:
        word_ids = []
        for text in texts:
          tokens = (token if token in self.words else self.oov
                    for token in text.split(' '))
          word_id = [self.words[token] for token in tokens]
          word_ids.append(word_id)
        label_graph = k2.linear_fsa(word_ids)
        decoding_graph = k2.connect(k2.intersect(label_graph,
                                                 self.L_inv)).invert_()
        decoding_graph = k2.arc_sort(decoding_graph)
        decoding_graph = k2.compose(self.ctc_topo, decoding_graph)
        decoding_graph = k2.connect(decoding_graph)
        # make sure the gradient is not accumulated
        decoding_graph.requires_grad_(False)
        return decoding_graph
