// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IApexVmERC20 {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function approve(address spender, uint256 value) external returns (bool);
}

interface IApexVmAaveV3Pool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IApexVmBalancerV2Vault {
    function flashLoan(
        address recipient,
        address[] calldata tokens,
        uint256[] calldata amounts,
        bytes calldata userData
    ) external;
}

interface IApexVmBalancerV3Vault {
    function unlock(bytes calldata data) external returns (bytes memory);
    function sendTo(IApexVmERC20 token, address to, uint256 amount) external;
    function settle(IApexVmERC20 token, uint256 amountHint) external returns (uint256);
}

contract ApexOmegaExecutionVMSplit {
    struct RouteStep {
        address venue;
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        uint256 minAmountOut;
        uint256 callValue;
        bytes payload;
    }

    struct ExecutionContext {
        address profitAsset;
        uint256 minNetProfit;
        uint256 nonce;
        bytes32 merkleRoot;
        bytes32[] proof;
        RouteStep[] steps;
    }

    struct C1State {
        uint256 blockNumber;
        bool executed;
    }

    address public immutable owner;
    address public immutable aaveV3Pool;
    address public immutable balancerV2Vault;
    address public immutable balancerV3Vault;
    address public platformTreasury;

    uint256 public constant BPS_SCALE = 10_000;
    uint256 public constant OWNER_PROFIT_BPS = 3_000;
    uint256 public constant TREASURY_PROFIT_BPS = 7_000;

    uint256 public globalNonce;
    bool private _locked;
    bool public isEmergencyStopped;

    mapping(address => bool) public authorizedRouters;
    mapping(address => bool) public authorizedPools;
    mapping(bytes32 => C1State) public c1ExecutionRegistry;

    event StrategyExecuted(bytes32 indexed internalId, uint256 profitRealized, uint256 timestamp);
    event ProfitSplit(
        bytes32 indexed internalId,
        address indexed ownerReceiver,
        address indexed treasuryReceiver,
        address profitAsset,
        uint256 ownerAmount,
        uint256 treasuryAmount
    );
    event C1Registered(bytes32 indexed internalId, uint256 blockNumber);
    event EmergencyStateChanged(bool isStopped);
    event RegistriesUpdated(address target, bool state, uint8 registryType);
    event PlatformTreasuryUpdated(address indexed previousTreasury, address indexed newTreasury);

    modifier onlyOwner() {
        require(msg.sender == owner, "ERR_NOT_OWNER");
        _;
    }

    modifier nonReentrant() {
        require(!_locked, "ERR_REENTRANCY");
        _locked = true;
        _;
        _locked = false;
    }

    modifier whenNotStopped() {
        require(!isEmergencyStopped, "ERR_EMERGENCY_STOP");
        _;
    }

    constructor(address _aaveV3Pool, address _balancerV2Vault, address _balancerV3Vault, address _platformTreasury) {
        require(_aaveV3Pool != address(0), "ERR_AAVE_POOL_ZERO");
        require(_balancerV2Vault != address(0), "ERR_BALANCER_V2_VAULT_ZERO");
        require(_balancerV3Vault != address(0), "ERR_BALANCER_V3_VAULT_ZERO");
        require(_platformTreasury != address(0), "ERR_TREASURY_ZERO");
        owner = msg.sender;
        aaveV3Pool = _aaveV3Pool;
        balancerV2Vault = _balancerV2Vault;
        balancerV3Vault = _balancerV3Vault;
        platformTreasury = _platformTreasury;
        emit PlatformTreasuryUpdated(address(0), _platformTreasury);
    }

    receive() external payable {}

    function setEmergencyStop(bool stop) external onlyOwner {
        isEmergencyStopped = stop;
        emit EmergencyStateChanged(stop);
    }

    function setPlatformTreasury(address newTreasury) external onlyOwner {
        require(newTreasury != address(0), "ERR_TREASURY_ZERO");
        address previousTreasury = platformTreasury;
        platformTreasury = newTreasury;
        emit PlatformTreasuryUpdated(previousTreasury, newTreasury);
    }

    function updateRegistries(address target, bool allowed, uint8 registryType) external onlyOwner {
        require(target != address(0), "ERR_TARGET_ZERO");
        if (registryType == 0) {
            authorizedRouters[target] = allowed;
        } else if (registryType == 1) {
            authorizedPools[target] = allowed;
        } else {
            revert("ERR_BAD_REGISTRY_TYPE");
        }
        emit RegistriesUpdated(target, allowed, registryType);
    }

    function updateRegistriesBatch(address[] calldata targets, bool allowed, uint8 registryType) external onlyOwner {
        for (uint256 i = 0; i < targets.length; i++) {
            address target = targets[i];
            require(target != address(0), "ERR_TARGET_ZERO");
            if (registryType == 0) {
                authorizedRouters[target] = allowed;
            } else if (registryType == 1) {
                authorizedPools[target] = allowed;
            } else {
                revert("ERR_BAD_REGISTRY_TYPE");
            }
            emit RegistriesUpdated(target, allowed, registryType);
        }
    }

    function executeC1(
        uint8 flashloanSource,
        address flashloanAsset,
        uint256 flashloanAmount,
        ExecutionContext calldata context
    ) external onlyOwner nonReentrant whenNotStopped {
        bytes32 internalId = keccak256(abi.encodePacked(block.number, msg.sender, context.nonce));
        require(c1ExecutionRegistry[internalId].blockNumber == 0, "ERR_C1_ALREADY_EXISTS");

        c1ExecutionRegistry[internalId] = C1State({
            blockNumber: block.number,
            executed: true
        });
        emit C1Registered(internalId, block.number);

        _initiateFlashloanPipeline(flashloanSource, flashloanAsset, flashloanAmount, context, internalId);
    }

    function executeC2(
        bytes32 c1InternalId,
        uint8 flashloanSource,
        address flashloanAsset,
        uint256 flashloanAmount,
        ExecutionContext calldata context
    ) external onlyOwner nonReentrant whenNotStopped {
        C1State memory state = c1ExecutionRegistry[c1InternalId];
        require(state.executed, "ERR_C1_NOT_FOUND");
        require(block.number > state.blockNumber, "ERR_C2_BEFORE_CONFIRMATION");
        require(block.number <= state.blockNumber + 5, "ERR_C2_EXPIRED");

        bytes32 currentExecutionId = keccak256(abi.encodePacked(block.number, c1InternalId, context.nonce));
        _initiateFlashloanPipeline(flashloanSource, flashloanAsset, flashloanAmount, context, currentExecutionId);
    }

    function _initiateFlashloanPipeline(
        uint8 source,
        address asset,
        uint256 amount,
        ExecutionContext calldata context,
        bytes32 executionId
    ) internal {
        require(asset != address(0), "ERR_ASSET_ZERO");
        require(amount > 0, "ERR_AMOUNT_ZERO");
        _verifyGuardInvariants(context);

        bytes memory encodedParams = abi.encode(context, asset, amount, executionId);

        if (source == 0) {
            IApexVmAaveV3Pool(aaveV3Pool).flashLoanSimple(address(this), asset, amount, encodedParams, 0);
        } else if (source == 1) {
            address[] memory assets = new address[](1);
            assets[0] = asset;
            uint256[] memory amounts = new uint256[](1);
            amounts[0] = amount;
            IApexVmBalancerV2Vault(balancerV2Vault).flashLoan(address(this), assets, amounts, encodedParams);
        } else if (source == 2) {
            IApexVmBalancerV3Vault(balancerV3Vault).unlock(encodedParams);
        } else {
            revert("ERR_UNSUPPORTED_FLASH_SOURCE");
        }
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        require(msg.sender == aaveV3Pool, "ERR_UNAUTHORIZED_CALLBACK");
        require(initiator == address(this), "ERR_UNAUTHORIZED_INITIATOR");

        (ExecutionContext memory context, , , bytes32 executionId) = abi.decode(
            params,
            (ExecutionContext, address, uint256, bytes32)
        );

        uint256 totalRepayment = amount + premium;
        _orchestrateRouteAndVerify(context, asset, totalRepayment, executionId, 0);

        _forceApprove(asset, aaveV3Pool, totalRepayment);
        return true;
    }

    function receiveFlashLoan(
        address[] calldata tokens,
        uint256[] calldata amounts,
        uint256[] calldata feeAmounts,
        bytes calldata userData
    ) external {
        require(msg.sender == balancerV2Vault, "ERR_UNAUTHORIZED_CALLBACK");
        require(tokens.length == 1 && amounts.length == 1 && feeAmounts.length == 1, "ERR_BAD_BALANCER_FLASH");

        (ExecutionContext memory context, address asset, uint256 amount, bytes32 executionId) = abi.decode(
            userData,
            (ExecutionContext, address, uint256, bytes32)
        );

        require(tokens[0] == asset, "ERR_ASSET_MISMATCH");
        require(amounts[0] == amount, "ERR_AMOUNT_MISMATCH");

        uint256 totalRepayment = amount + feeAmounts[0];
        _orchestrateRouteAndVerify(context, asset, totalRepayment, executionId, 1);
    }

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        require(msg.sender == balancerV3Vault, "ERR_UNAUTHORIZED_CALLBACK");
        (ExecutionContext memory context, address asset, uint256 amount, bytes32 executionId) = abi.decode(
            data,
            (ExecutionContext, address, uint256, bytes32)
        );
        IApexVmBalancerV3Vault(balancerV3Vault).sendTo(IApexVmERC20(asset), address(this), amount);
        _orchestrateRouteAndVerify(context, asset, amount, executionId, 2);
        return "";
    }

    function _orchestrateRouteAndVerify(
        ExecutionContext memory context,
        address flashloanAsset,
        uint256 totalRepayment,
        bytes32 executionId,
        uint8 repaymentMode
    ) internal {
        require(flashloanAsset == context.profitAsset, "ERR_FLASH_ASSET_MUST_EQUAL_PROFIT_ASSET");
        uint256 balanceBefore = IApexVmERC20(context.profitAsset).balanceOf(address(this));
        require(balanceBefore >= totalRepayment, "ERR_FLASH_NOT_RECEIVED");

        for (uint256 i = 0; i < context.steps.length; i++) {
            RouteStep memory step = context.steps[i];
            require(step.venue != address(0), "ERR_STEP_VENUE_ZERO");
            require(step.tokenIn != address(0) && step.tokenOut != address(0), "ERR_STEP_TOKEN_ZERO");
            require(authorizedRouters[step.venue] || authorizedPools[step.venue], "ERR_VENUE_NOT_AUTHORIZED");

            uint256 tokenInBalance = IApexVmERC20(step.tokenIn).balanceOf(address(this));
            require(tokenInBalance >= step.amountIn, "ERR_INSUFFICIENT_HOP_BALANCE");

            _forceApprove(step.tokenIn, step.venue, step.amountIn);

            uint256 outBefore = IApexVmERC20(step.tokenOut).balanceOf(address(this));
            (bool success, bytes memory reason) = step.venue.call{value: step.callValue}(step.payload);
            require(success, _bubbleReason(reason));

            uint256 outAfter = IApexVmERC20(step.tokenOut).balanceOf(address(this));
            uint256 outputRealized = outAfter > outBefore ? outAfter - outBefore : 0;
            require(outputRealized >= step.minAmountOut, "ERR_MIN_OUTPUT_NOT_MET");
        }

        uint256 afterRoute = IApexVmERC20(context.profitAsset).balanceOf(address(this));
        require(afterRoute >= totalRepayment + context.minNetProfit, "ERR_PROFIT_GUARD_VIOLATION");

        if (repaymentMode == 1) {
            _safeTransfer(context.profitAsset, msg.sender, totalRepayment);
        } else if (repaymentMode == 2) {
            _safeTransfer(context.profitAsset, balancerV3Vault, totalRepayment);
            IApexVmBalancerV3Vault(balancerV3Vault).settle(IApexVmERC20(context.profitAsset), totalRepayment);
        }

        uint256 afterRepayment = repaymentMode == 0
            ? afterRoute - totalRepayment
            : IApexVmERC20(context.profitAsset).balanceOf(address(this));
        uint256 baselineAfterLoanRemoved = balanceBefore - totalRepayment;
        require(afterRepayment >= baselineAfterLoanRemoved + context.minNetProfit, "ERR_NO_PROFIT_GENERATED");

        uint256 netProfitRealized = afterRepayment - baselineAfterLoanRemoved;
        _splitProfit(context.profitAsset, netProfitRealized, executionId);

        emit StrategyExecuted(executionId, netProfitRealized, block.timestamp);
    }

    function _splitProfit(address profitAsset, uint256 netProfitRealized, bytes32 executionId) internal {
        if (netProfitRealized == 0) return;
        address treasury = platformTreasury;
        require(treasury != address(0), "ERR_TREASURY_ZERO");

        uint256 ownerAmount = (netProfitRealized * OWNER_PROFIT_BPS) / BPS_SCALE;
        uint256 treasuryAmount = netProfitRealized - ownerAmount;

        if (ownerAmount > 0) {
            _safeTransfer(profitAsset, owner, ownerAmount);
        }
        if (treasuryAmount > 0) {
            _safeTransfer(profitAsset, treasury, treasuryAmount);
        }

        emit ProfitSplit(executionId, owner, treasury, profitAsset, ownerAmount, treasuryAmount);
    }

    function _verifyGuardInvariants(ExecutionContext calldata context) internal {
        require(context.nonce == globalNonce, "ERR_NONCE_GUARD_MISMATCH");
        globalNonce++;

        if (context.merkleRoot != bytes32(0)) {
            bytes32 leaf = keccak256(abi.encodePacked(context.profitAsset, context.minNetProfit, context.nonce));
            require(_verifyProof(context.proof, context.merkleRoot, leaf), "ERR_MERKLE_GUARD_INVALID");
        } else {
            require(context.proof.length == 0, "ERR_EMPTY_ROOT_WITH_PROOF");
        }
    }

    function _verifyProof(
        bytes32[] memory proof,
        bytes32 root,
        bytes32 leaf
    ) internal pure returns (bool) {
        bytes32 computedHash = leaf;
        for (uint256 i = 0; i < proof.length; i++) {
            bytes32 proofElement = proof[i];
            if (computedHash <= proofElement) {
                computedHash = keccak256(abi.encodePacked(computedHash, proofElement));
            } else {
                computedHash = keccak256(abi.encodePacked(proofElement, computedHash));
            }
        }
        return computedHash == root;
    }

    function _forceApprove(address token, address spender, uint256 amount) internal {
        _safeApprove(token, spender, 0);
        _safeApprove(token, spender, amount);
    }

    function _safeApprove(address token, address spender, uint256 amount) internal {
        (bool success, bytes memory data) = token.call(
            abi.encodeWithSelector(IApexVmERC20.approve.selector, spender, amount)
        );
        require(success && (data.length == 0 || abi.decode(data, (bool))), "ERR_APPROVE_FAILED");
    }

    function _safeTransfer(address token, address to, uint256 amount) internal {
        (bool success, bytes memory data) = token.call(
            abi.encodeWithSelector(IApexVmERC20.transfer.selector, to, amount)
        );
        require(success && (data.length == 0 || abi.decode(data, (bool))), "ERR_TRANSFER_FAILED");
    }

    function _bubbleReason(bytes memory reason) internal pure returns (string memory) {
        if (reason.length < 68) return "ERR_VENUE_PAYLOAD_REVERTED";
        assembly {
            reason := add(reason, 0x04)
        }
        return abi.decode(reason, (string));
    }

    function emergencyRecoverAsset(address token, uint256 amount) external onlyOwner {
        require(isEmergencyStopped, "ERR_MUST_BE_STOPPED");
        _safeTransfer(token, owner, amount);
    }
}
