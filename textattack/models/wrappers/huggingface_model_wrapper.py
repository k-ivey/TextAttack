import torch
from torch.nn import CrossEntropyLoss
import transformers

import textattack

from .pytorch_model_wrapper import PyTorchModelWrapper


class HuggingFaceModelWrapper(PyTorchModelWrapper):
    """Loads a HuggingFace ``transformers`` model and tokenizer."""

    def __init__(self, model, tokenizer, loss=CrossEntropyLoss(), batch_size=32):
        self.model = model.to(textattack.shared.utils.device)
        if isinstance(tokenizer, transformers.PreTrainedTokenizer):
            tokenizer = textattack.models.tokenizers.AutoTokenizer(tokenizer=tokenizer)
        self.tokenizer = tokenizer
        self.loss_fn = loss
        self.batch_size = batch_size

    def _model_predict(self, inputs):
        """Turn a list of dicts into a dict of lists.

        Then make lists (values of dict) into tensors.
        """
        model_device = next(self.model.parameters()).device
        input_dict = {k: [_dict[k] for _dict in inputs] for k in inputs[0]}
        input_dict = {
            k: torch.tensor(v).to(model_device) for k, v in input_dict.items()
        }
        outputs = self.model(**input_dict)

        if isinstance(outputs[0], str):
            # HuggingFace sequence-to-sequence models return a list of
            # string predictions as output. In this case, return the full
            # list of outputs.
            return outputs
        else:
            # HuggingFace classification models return a tuple as output
            # where the first item in the tuple corresponds to the list of
            # scores for each input.
            return outputs[0]

    def __call__(self, text_input_list):
        """Passes inputs to HuggingFace models as keyword arguments.

        (Regular PyTorch ``nn.Module`` models typically take inputs as
        positional arguments.)
        """
        ids = self.tokenize(text_input_list)

        with torch.no_grad():
            outputs = textattack.shared.utils.batch_model_predict(
                self._model_predict, ids, batch_size=self.batch_size
            )

        return outputs

    def get_grads(self, text_input_list):
        """
        Get gradient w.r.t. embedding layer
        Args:
            text_input_list (list[str]): list of input strings
        Returns:
            list of gradient as torch.Tensor
        """
        if isinstance(self.model, textattack.models.helpers.T5ForTextToText):
            raise NotImplementedError("`get_grads` for T5FotTextToText has not been implemented yet.")
        
        self.model.train()
        embedding_layer = self.model.get_input_embeddings()
        original_state = embedding_layer.weight.requires_grad
        embedding_layer.weight.requires_grad = True

        emb_grads = []
        def grad_hook(module, grad_in, grad_out):
            emb_grads.append(grad_out[0])
        
        emb_hook = embedding_layer.register_backward_hook(grad_hook)

        self.model.zero_grad()
        model_device = next(self.model.parameters()).device
        ids = self.tokenize(text_input_list)
        tokens = [self.tokenizer.convert_ids_to_tokens(_ids["input_ids"]) for _ids in ids]

        predictions = self._model_predict(ids)
        original_label = predictions.argmax(dim=1)

        loss = self.loss_fn(predictions, original_label)
        loss.backward()

        # grad w.r.t to word embeddings
        grad = emb_grads[0]

        if len(grad.shape) > 2:
            grad = list(torch.transpose(grad, 0, 1).unbind(dim=0))
        else:
            grad = [grad]

        embedding_layer.weight.requires_grad = original_state
        emb_hook.remove()

        output = {"tokens": tokens, "ids": ids, "gradient": grad}

        return output

