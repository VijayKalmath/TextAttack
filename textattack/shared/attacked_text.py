from collections import OrderedDict
import math

import numpy as np
import torch

from .utils import device, words_from_text


class AttackedText:

    """ 
     A helper class that represents a string that can be attacked.
     
     Models that take multiple sentences as input separate them by ``SPLIT_TOKEN``. 
     Attacks "see" the entire input, joined into one string, without the split token. 
     
     ``AttackedText`` instances that were perturbed from other ``AttackedText``
     objects contain a pointer to the previous text 
     (``attack_attrs["previous_attacked_text"]``), so that the full chain of 
     perturbations might be reconstructed by using this key to form a linked
     list.

     Args:
        text (string): The string that this AttackedText represents
        attack_attrs (dict): Dictionary of various attributes stored
            during the course of an attack.
        
    """

    SPLIT_TOKEN = ">>>>"

    def __init__(self, text_input, attack_attrs=None):
        # Read in ``text_input`` as a string or OrderedDict.
        if isinstance(text_input, str):
            self._text_input = OrderedDict([("text", text_input)])
        elif isinstance(text_input, OrderedDict):
            self._text_input = text_input
        else:
            raise TypeError(
                f"Invalid text_input type {type(text_input)} (required str or OrderedDict)"
            )
        # Format text inputs.
        self._text_input = {k: v.strip() for k, v in self._text_input.items()}
        self.words = words_from_text(self.text)
        if attack_attrs is None:
            self.attack_attrs = dict()
        elif isinstance(attack_attrs, dict):
            self.attack_attrs = attack_attrs
        else:
            raise TypeError(f"Invalid type for attack_attrs: {type(attack_attrs)}")
        # Indices of words from the *original* text. Allows us to map
        # indices between original text and this text, and vice-versa.
        self.attack_attrs.setdefault("original_index_map", np.arange(len(self.words)))
        # A list of all indices in *this* text that have been modified.
        self.attack_attrs.setdefault("modified_indices", set())

    def __eq__(self, other):
        """ Compares two text instances to make sure they have the same attack
            attributes.
            
            Since some elements stored in ``self.attack_attrs`` may be numpy
            arrays, we have to take special care when comparing them.
        """
        if not (self.text == other.text):
            return False
        for key in self.attack_attrs:
            if key not in other.attack_attrs:
                return False
            elif isinstance(self.attack_attrs[key], np.ndarray):
                if not (self.attack_attrs[key].shape == other.attack_attrs[key].shape):
                    return False
                elif not (self.attack_attrs[key] == other.attack_attrs[key]).all():
                    return False
            else:
                if not self.attack_attrs[key] == other.attack_attrs[key]:
                    return False
        return True

    def __hash__(self):
        return hash(self.text)

    def free_memory(self):
        """ Delete items that take up memory.
            
            Can be called once the AttackedText is only needed to display.
        """
        if "previous_attacked_text" in self.attack_attrs:
            self.attack_attrs["previous_attacked_text"].free_memory()
        if "last_transformation" in self.attack_attrs:
            del self.attack_attrs["last_transformation"]
        for key in self.attack_attrs:
            if isinstance(self.attack_attrs[key], torch.Tensor):
                del self.attack_attrs[key]

    def text_window_around_index(self, index, window_size):
        """ The text window of ``window_size`` words centered around ``index``. """
        length = len(self.words)
        half_size = (window_size - 1) / 2.0
        if index - half_size < 0:
            start = 0
            end = min(window_size - 1, length - 1)
        elif index + half_size >= length:
            start = max(0, length - window_size)
            end = length - 1
        else:
            start = index - math.ceil(half_size)
            end = index + math.floor(half_size)
        text_idx_start = self._text_index_of_word_index(start)
        text_idx_end = self._text_index_of_word_index(end) + len(self.words[end])
        return self.text[text_idx_start:text_idx_end]

    def _text_index_of_word_index(self, i):
        """ Returns the index of word ``i`` in self.text. """
        pre_words = self.words[: i + 1]
        lower_text = self.text.lower()
        # Find all words until `i` in string.
        look_after_index = 0
        for word in pre_words:
            look_after_index = lower_text.find(word.lower(), look_after_index)
        return look_after_index

    def text_until_word_index(self, i):
        """ Returns the text before the beginning of word at index ``i``. """
        look_after_index = self._text_index_of_word_index(i)
        return self.text[:look_after_index]

    def text_after_word_index(self, i):
        """ Returns the text after the end of word at index ``i``. """
        # Get index of beginning of word then jump to end of word.
        look_after_index = self._text_index_of_word_index(i) + len(self.words[i])
        return self.text[look_after_index:]

    def first_word_diff(self, other_attacked_text):
        """ Returns the first word in self.words that differs from 
            other_attacked_text. Useful for word swap strategies. """
        w1 = self.words
        w2 = other_attacked_text.words
        for i in range(min(len(w1), len(w2))):
            if w1[i] != w2[i]:
                return w1
        return None

    def first_word_diff_index(self, other_attacked_text):
        """ Returns the index of the first word in self.words that differs
            from other_attacked_text. Useful for word swap strategies. """
        w1 = self.words
        w2 = other_attacked_text.words
        for i in range(min(len(w1), len(w2))):
            if w1[i] != w2[i]:
                return i
        return None

    def all_words_diff(self, other_attacked_text):
        """ Returns the set of indices for which this and other_attacked_text
        have different words. """
        indices = set()
        w1 = self.words
        w2 = other_attacked_text.words
        for i in range(min(len(w1), len(w2))):
            if w1[i] != w2[i]:
                indices.add(i)
        return indices

    def ith_word_diff(self, other_attacked_text, i):
        """ Returns whether the word at index i differs from other_attacked_text
        """
        w1 = self.words
        w2 = other_attacked_text.words
        if len(w1) - 1 < i or len(w2) - 1 < i:
            return True
        return w1[i] != w2[i]

    def convert_from_original_idxs(self, idxs):
        """ Takes indices of words from original string and converts them to 
            indices of the same words in the current string.
            
            Uses information from ``self.attack_attrs['original_index_map'], 
            which maps word indices from the original to perturbed text.
        """
        if len(self.attack_attrs["original_index_map"]) == 0:
            return idxs
        elif isinstance(idxs, set):
            idxs = list(idxs)
        if isinstance(idxs, list) or isinstance(idxs, np.ndarray):
            idxs = torch.tensor(idxs)
        elif not isinstance(idxs, torch.Tensor):
            raise TypeError(
                f"convert_from_original_idxs got invalid idxs type {type(idxs)}"
            )
        return [self.attack_attrs["original_index_map"][i] for i in idxs]

    def replace_words_at_indices(self, indices, new_words):
        """ This code returns a new AttackedText object where the word at 
            ``index`` is replaced with a new word."""
        if len(indices) != len(new_words):
            raise ValueError(
                f"Cannot replace {len(new_words)} words at {len(indices)} indices."
            )
        words = self.words[:]
        for i, new_word in zip(indices, new_words):
            if not isinstance(i, int):
                try:
                    i = int(i)
                except:
                    raise TypeError(
                        f"replace_words_at_indices requires ``int`` indices, got {type(i)}"
                    )
            if not isinstance(new_word, str):
                raise TypeError(
                    f"replace_words_at_indices requires ``str`` words, got {type(new_word)}"
                )
            if (i < 0) or (i > len(words)):
                raise ValueError(f"Cannot assign word at index {i}")
            words[i] = new_word
        return self.replace_new_words(words)

    def replace_word_at_index(self, index, new_word):
        """ This code returns a new AttackedText object where the word at 
            ``index`` is replaced with a new word.
        """
        if not isinstance(index, int):
            try:
                index = int(index)
            except:
                raise TypeError(
                    f"replace_word_at_index requires ``int`` index, got {type(index)}"
                )
        if not isinstance(new_word, str):
            raise TypeError(
                f"replace_word_at_index requires ``str`` new_word, got {type(new_word)}"
            )
        return self.replace_words_at_indices([index], [new_word])

    def delete_word_at_index(self, index):
        """ This code returns a new AttackedText object where the word at 
            ``index`` is removed.
        """
        return self.replace_word_at_index(index, "")

    def get_deletion_indices(self):
        return self.attack_attrs["original_index_map"][
            self.attack_attrs["original_index_map"] == -1
        ]

    def replace_new_words(self, new_words):
        """ This code returns a new AttackedText object and replaces old list 
            of words with a new list of words, but preserves the punctuation 
            and spacing of the original message.
            
            ``self.words`` is a list of the words in the current text with 
            punctuation removed. However, each "word" in ``new_words``
            could be an empty string, representing a word deletion, or a string
            with multiple space-separated words, representation an insertion
            of one or more words.
        """
        perturbed_text = ""
        original_text = AttackedText.SPLIT_TOKEN.join(self._text_input.values())
        new_attack_attrs = dict()
        new_attack_attrs["newly_modified_indices"] = set()
        # Point to previously monitored text.
        new_attack_attrs["previous_attacked_text"] = self
        # Use `new_attack_attrs` to track indices with respect to the original
        # text.
        new_attack_attrs["modified_indices"] = self.attack_attrs[
            "modified_indices"
        ].copy()
        new_attack_attrs["original_index_map"] = self.attack_attrs[
            "original_index_map"
        ].copy()
        new_i = 0
        # Create the new attacked text by swapping out words from the original
        # text with a sequence of 0+ words in the new text.
        for i, (input_word, adv_word_seq) in enumerate(zip(self.words, new_words)):
            word_start = original_text.index(input_word)
            word_end = word_start + len(input_word)
            perturbed_text += original_text[:word_start]
            original_text = original_text[word_end:]
            adv_num_words = len(words_from_text(adv_word_seq))
            num_words_diff = adv_num_words - len(words_from_text(input_word))
            # Track indices on insertions and deletions.
            if num_words_diff != 0:
                # Re-calculated modified indices. If words are inserted or deleted,
                # they could change.
                shifted_modified_indices = set()
                for modified_idx in new_attack_attrs["modified_indices"]:
                    if modified_idx < i:
                        shifted_modified_indices.add(modified_idx)
                    elif modified_idx > i:
                        shifted_modified_indices.add(modified_idx + num_words_diff)
                    else:
                        pass
                new_attack_attrs["modified_indices"] = shifted_modified_indices
                # Track insertions and deletions wrt original text.
                original_modification_idx = i
                new_idx_map = new_attack_attrs["original_index_map"].copy()
                if num_words_diff == -1:
                    new_idx_map[new_idx_map == i] = -1
                new_idx_map[new_idx_map > i] += num_words_diff
                new_attack_attrs["original_index_map"] = new_idx_map
            # Move pointer and save indices of new modified words.
            for j in range(i, i + adv_num_words):
                if input_word != adv_word_seq:
                    new_attack_attrs["modified_indices"].add(new_i)
                    new_attack_attrs["newly_modified_indices"].add(new_i)
                new_i += 1
            # Check spaces for deleted text.
            if adv_num_words == 0:
                # Remove extra space (or else there would be two spaces for each
                # deleted word).
                # @TODO What to do with punctuation in this case? This behavior is undefined.
                if i == 0:
                    # If the first word was deleted, take a subsequent space.
                    if original_text[0] == " ":
                        original_text = original_text[1:]
                else:
                    # If a word other than the first was deleted, take a preceding space.
                    if perturbed_text[-1] == " ":
                        perturbed_text = perturbed_text[:-1]
            # Add substitute word(s) to new sentence.
            perturbed_text += adv_word_seq
        perturbed_text += original_text  # Add all of the ending punctuation.
        # Reform perturbed_text into an OrderedDict.
        perturbed_input_texts = perturbed_text.split(AttackedText.SPLIT_TOKEN)
        perturbed_input = OrderedDict(
            zip(self._text_input.keys(), perturbed_input_texts)
        )
        return AttackedText(perturbed_input, attack_attrs=new_attack_attrs)

    @property
    def tokenizer_input(self):
        """ The tuple of inputs to be passed to the tokenizer. """
        return tuple(self._text_input.values())

    @property
    def text(self):
        """ Represents full text input. Multiply inputs are joined with a line 
            break.
        """
        return "\n".join(self._text_input.values())

    @property
    def printable_text(self):
        """ Represents full text input. Adds field descriptions.
        
        For example, entailment inputs look like:
            ```
            premise: ...
            hypothesis: ...
            ```
        """
        # For single-sequence inputs, don't show a prefix.
        if len(self._text_input) == 1:
            return next(iter(self._text_input.values()))
        # For multiple-sequence inputs, show a prefix and a colon.
        else:
            return "\n\n".join(
                f"{key.capitalize()}: {value}"
                for key, value in self._text_input.items()
            )

    def __repr__(self):
        return f'<AttackedText "{self.text}">'
