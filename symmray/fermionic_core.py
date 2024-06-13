import autoray as ar

from .symmetric_core import (
    SymmetricArray,
    permuted,
    tensordot_symmetric,
    without,
)
from .interface import tensordot
from .symmetries import calc_phase_permutation, get_symmetry

_fermionic_array_slots = SymmetricArray.__slots__ + ("_phases",)


class FermionicArray(SymmetricArray):
    """ """

    __slots__ = _fermionic_array_slots

    def __init__(
        self,
        indices,
        charge_total,
        blocks=(),
    ):
        super().__init__(
            indices=indices, charge_total=charge_total, blocks=blocks
        )
        self._phases = {}

    @property
    def phases(self):
        """The lazy phases of each sector. Trivial phases are not necessarily
        stored.
        """
        try:
            return self._phases
        except AttributeError:
            self._phases = {}
            return self._phases

    def copy(self):
        """Create a copy of the fermionic array.

        Returns
        -------
        FermionicArray
            The copied array.
        """
        new = super().copy()
        new._phases = self.phases.copy()
        return new

    def copy_with(
        self, indices=None, blocks=None, charge_total=None, phases=None
    ):
        new = super().copy_with(
            indices=indices, blocks=blocks, charge_total=charge_total
        )
        new._phases = self._phases.copy() if phases is None else phases
        return new

    def transpose(self, axes=None, phase=True, inplace=False):
        """Transpose the fermionic array, by default accounting for the phases
        accumulated from swapping odd charges.

        Parameters
        ----------
        axes : tuple of int, optional
            The permutation of axes, by default None.
        phase : bool, optional
            Whether to flip the phase of sectors whose odd charges undergo a
            odd permutation. By default True.
        inplace : bool, optional
            Whether to perform the operation in place.

        Returns
        -------
        FermionicArray
            The transposed array.
        """
        new = self if inplace else self.copy()

        if axes is None:
            axes = tuple(range(new.ndim - 1, -1, -1))

        if phase:
            # compute new sector phases
            permuted_phases = {}
            for sector in new.sectors:
                parities = tuple(new.symmetry.parity(q) for q in sector)
                perm_phase = calc_phase_permutation(parities, axes)
                new_phase = new._phases.get(sector, 1) * perm_phase
                if new_phase == -1:
                    # only populate non-trivial phases
                    permuted_phases[permuted(sector, axes)] = -1
            new._phases = permuted_phases

        else:
            # just permute the phase keys
            new._phases = {
                permuted(sector, axes): phase
                for sector, phase in new._phases.items()
            }

        # transpose block arrays
        SymmetricArray.transpose(new, axes, inplace=True)

        return new

    def phase_flip(self, *axs, inplace=False):
        """Flip the phase of all sectors with odd parity at the given axis.

        Parameters
        ----------
        ax : int
            The axis along which to flip the phase.
        inplace : bool, optional
            Whether to perform the operation in place.

        Returns
        -------
        FermionicArray
            The phase-flipped array.
        """
        new = self if inplace else self.copy()

        if not axs:
            # nothing to do
            return new

        new_phases = new.phases.copy()

        for sector in new.sectors:
            parity = sum(new.symmetry.parity(sector[ax]) for ax in axs) % 2

            if parity:
                new_phase = -new_phases.get(sector, 1)
                if new_phase == 1:
                    new_phases.pop(sector, None)
                else:
                    # only keep non-trivial phases
                    new_phases[sector] = new_phase

        new._phases = new_phases

        return new

    def phase_resolve(self, inplace=False):
        """Multiply all lazy phases into the block arrays.

        Parameters
        ----------
        inplace : bool, optional
            Whether to perform the operation in place.

        Returns
        -------
        FermionicArray
            The resolved array, which now has no lazy phases.
        """
        new = self if inplace else self.copy()
        while new._phases:
            sector, phase = new._phases.popitem()
            if phase == -1:
                new._blocks[sector] = -new._blocks[sector]
        return new

    def phase_virtual_transpose(self, axes=None, inplace=False):
        """Phase this fermionic array as if it were transposed virtually, i.e.
        the actual arrays are not transposed.

        Parameters
        ----------
        axes : tuple of int, optional
            The permutation of axes, by default None.
        inplace : bool, optional
            Whether to perform the operation in place.

        Returns
        -------
        FermionicArray
        """
        new = self if inplace else self.copy()

        for sector in new.sectors:
            parities = tuple(new.symmetry.parity(q) for q in sector)

            phase_new = (
                # start with old phase
                new._phases.get(sector, 1)
                *
                # get the phase from permutation
                calc_phase_permutation(parities, axes)
            )

            if phase_new == 1:
                new._phases.pop(sector, None)
            else:
                new._phases[sector] = phase_new

        return new

    def conj(self, phase=True, inplace=False):
        """Conjugate this fermionic array. By default this include phases from
        both the virtual flipping of all axes, and the conjugation of 'bra'
        (flow=True) indices, such that::

            (
                tensordot_fermionic(x.conj(), x, ndim) ==
                tensordot_fermionic(x, x.conj(), ndim)
            )

        Parameters
        ----------
        phase : bool, optional
            Whether to compute fermionic phases.
        """
        new = self if inplace else self.copy()

        _conj = ar.get_lib_fn(new.backend, "conj")

        new._indices = tuple(ix.conj() for ix in new.indices)
        axs_conj = tuple(ax for ax, ix in enumerate(new._indices) if ix.flow)

        for sector, array in new.blocks.items():
            # conjugate the actual array
            new.blocks[sector] = _conj(array)

            if phase:
                parities = tuple(map(new.symmetry.parity, sector))

                phase_new = (
                    # start with old phase
                    new._phases.get(sector, 1)
                    # get the phase from 'virtually' reversing all axes:
                    #     (perm=[ndim-1, ..., 0])
                    * calc_phase_permutation(parities, None)
                    # get the phase from conjugating 'bra' indices
                    * (-1 if (sum(parities[ax] for ax in axs_conj) % 2) else 1)
                )

                if phase_new == 1:
                    new._phases.pop(sector, None)
                else:
                    new._phases[sector] = phase_new

        return new

    def dagger(self, phase=True, inplace=False):
        """Fermionic adjoint.

        Parameters
        ----------
        phase : bool, optional
            Whether to flip the phase conjugate indices, by default True.
        inplace : bool, optional
            Whether to perform the operation in place.

        Returns
        -------
        FermionicArray
            The conjugate transposed array.
        """
        new = self if inplace else self.copy()

        _conj = ar.get_lib_fn(new.backend, "conj")
        _transpose = ar.get_lib_fn(new.backend, "transpose")

        new_indices = tuple(ix.conj() for ix in reversed(new.indices))

        # conjugate transpose all arrays
        new_blocks = {}
        new_phases = {}
        for sector, array in new.blocks.items():

            new_sector = sector[::-1]

            if new._phases.pop(sector, 1) == -1:
                new_phases[new_sector] = -1

            new_blocks[new_sector] = _transpose(_conj(array))

        new._indices = new_indices
        new._blocks = new_blocks
        new._phases = new_phases

        if phase:
            axs_conj = tuple(
                ax for ax, ix in enumerate(new_indices) if ix.flow
            )
            new.phase_flip(*axs_conj, inplace=True)

        return new

    @property
    def H(self):
        """Fermionic conjugate transpose."""
        return self.dagger()

    def fuse(self, *axes_groups):
        from symmray.symmetric_core import calc_fuse_info

        new = self.copy()

        # first make groups into contiguous blocks using fermionic transpose
        perm = calc_fuse_info(axes_groups, new.flows)[2]
        new.transpose(perm, inplace=True)
        axes_groups = tuple(tuple(map(perm.index, g)) for g in axes_groups)

        # make sure all axes in each group are phase matched
        axes_flip = []
        virtual_perm = None
        for group in axes_groups:
            if group:
                if new.indices[group[0]].flow:
                    # overall dual index, flip
                    for ax in group:
                        if not new.indices[ax].flow:
                            axes_flip.append(ax)

                    if virtual_perm is None:
                        virtual_perm = list(range(new.ndim))

                    for axi, axj in zip(group, reversed(group)):
                        virtual_perm[axi] = axj

        if axes_flip:
            # print("fuse flips:", axes_flip)
            new.phase_flip(*axes_flip, inplace=True)

        # if the fused axes is overall bra, need phases from effective flip
        #   <a|<b|<c|  |a>|b>|c>    ->    P * <c|<b|<a|  |a>|b>|c>
        if virtual_perm is not None:
            print("fuse virtual_perm:", virtual_perm)
            new = new.phase_virtual_transpose(
                tuple(virtual_perm), inplace=True
            )

        new.phase_resolve(inplace=True)

        return SymmetricArray.fuse(new, *axes_groups)

    def unfuse(self, axis):
        raise NotImplementedError
        # sub_indices = self.indices[axis].subinfo.indices
        # flow0 = sub_indices[0].flow
        # axes_flip = [
        #     axis + i for i, ix in enumerate(sub_indices) if (ix.flow != flow0)
        # ]

        # new = super().unfuse(axis)

        # if axes_flip:
        #     print("unfuse flips:", axes_flip)
        #     new.phase_flip(*axes_flip, inplace=True)

        # new.phase_resolve(inplace=True)

        # return new

    def to_dense(self):
        """Return dense representation of the fermionic array, with lazy phases
        multiplied in.
        """
        return SymmetricArray.to_dense(self.phase_resolve())


@tensordot.register(FermionicArray)
def tensordot_fermionic(a, b, axes=2, **kwargs):
    """Contract two fermionic arrays along the specified axes, accounting for
    phases from both transpositions and contractions.

    Parameters
    ----------
    a : FermionicArray
        The first fermionic array.
    b : FermionicArray
        The second fermionic array.
    axes : int or (tuple[int], tuple[int]), optional
        The axes to contract over, by default 2.
    """
    ndim_a = a.ndim
    ndim_b = b.ndim

    # parse the axes argument for single integer and also negative indices
    if isinstance(axes, int):
        axes_a = tuple(range(ndim_a - axes, ndim_a))
        axes_b = tuple(range(0, axes))
    else:
        axes_a, axes_b = axes
        axes_a = tuple(x % ndim_a for x in axes_a)
        axes_b = tuple(x % ndim_b for x in axes_b)
        if not len(axes_a) == len(axes_b):
            raise ValueError("Axes must have same length.")

    left_axes = without(range(ndim_a), axes_a)
    right_axes = without(range(ndim_b), axes_b)
    ncon = len(axes_a)

    # permute a & b so we have axes like [..., x, y, z], [z, y, x, ...]
    perm_a = (*left_axes, *axes_a)
    # perm_b = (*reversed(axes_b), *right_axes)
    # perm_b = (*axes_b, *right_axes)
    # perm_a = (*left_axes, *reversed(axes_a))
    perm_b = (*axes_b, *right_axes)
    a = a.transpose(perm_a)
    b = b.transpose(perm_b)

    # new axes for tensordot_symmetric having permuted inputs
    new_axes_a = tuple(range(ndim_a - ncon, ndim_a))
    # new_axes_b = tuple(range(ncon - 1, -1, -1))
    # new_axes_b = tuple(range(ncon))
    # new_axes_a = tuple(reversed(range(ndim_a - ncon, ndim_a)))
    new_axes_b = tuple(range(ncon))

    b.phase_virtual_transpose(
        (*reversed(range(ncon)), *range(ncon, b.ndim)), inplace=True
    )

    # use reverse flows to calculate phase_flips
    if a.size <= b.size:
        axs_flip = tuple(ax for ax in new_axes_a if a.indices[ax].flow)
        a.phase_flip(*axs_flip, inplace=True)
    else:
        axs_flip = tuple(ax for ax in new_axes_b if not b.indices[ax].flow)
        b.phase_flip(*axs_flip, inplace=True)

    # actually multiply block arrays with phases
    a.phase_resolve(inplace=True)
    b.phase_resolve(inplace=True)

    # perform blocked contraction!
    c = tensordot_symmetric(a, b, axes=(new_axes_a, new_axes_b), **kwargs)

    return c


# --------------- specific fermionic symmetric array classes ---------------- #


class Z2FermionicArray(FermionicArray):
    __slots__ = _fermionic_array_slots
    symmetry = get_symmetry("Z2")

    def to_pyblock3(self):
        from pyblock3.algebra.fermion_symmetry import Z2
        from pyblock3.algebra.fermion import SparseFermionTensor, SubTensor

        blocks = [
            SubTensor(
                -array if self.phases.get(sector, 1) == -1 else array,
                q_labels=tuple(map(Z2, sector)),
            )
            for sector, array in self.blocks.items()
        ]

        data = SparseFermionTensor(
            blocks,
            pattern=["-" if f else "+" for f in self.flows],
        )

        data.shape = self.shape

        return data


class U1FermionicArray(FermionicArray):
    __slots__ = _fermionic_array_slots
    symmetry = get_symmetry("U1")
